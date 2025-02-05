import enum
import json
import math
import time
import httpx
from config import Config
import logging
import paho.mqtt.client as mqtt

log = logging.getLogger('charger')

class BatteryLoadStrategy(enum.Enum):
    PeakShaving = 'PeakShaving'
    PeakShavingMininal = 'PeakShavingMininal'
    Reserve = 'Reserve'
    NoCharging = 'NoCharging'

    def from_soc(soc: float, c: Config) -> 'BatteryLoadStrategy':
        if soc < c.battery_soc_no_charging:
            return BatteryLoadStrategy.NoCharging
        if soc < c.battery_soc_reserve:
            return BatteryLoadStrategy.Reserve
        if soc < c.battery_soc_peak_shaving_minimal:
            return BatteryLoadStrategy.PeakShavingMininal
        return BatteryLoadStrategy.PeakShaving

    def max_charing_power_with_grid(self, c: Config) -> float:
        if self == BatteryLoadStrategy.NoCharging:
            return 0
        if self == BatteryLoadStrategy.Reserve:
            return c.battery_power_reserve
        if self == BatteryLoadStrategy.PeakShavingMininal:
            return c.battery_power_peak_shaving_minimal
        return c.battery_power_peak_shaving

class ChargingPowerSource(enum.Enum):
    NoCharing = 'NoCharging' # Do not charge
    SolarOnly = 'SolarOnly' # Only charge when there is solar power
    MinPlusSolar = 'MinPlusSolar' # Charge with min power (grid) plus solar
    MinBatteryLoad = 'MinBatteryLoad' # Charge with grid and minimal battery usage
    Full = 'Full' # Charge with grid, solar and battery

    def get_max_power(self, c: Config, pv_power: float, total_load: float, battery_load: float, battery_strategy: BatteryLoadStrategy) -> float:
        if battery_strategy.max_charing_power_with_grid(c) < c.min_power * c.charge_efficiency_factor:
            log.warning('Not enough power with battery strategy')
            return 0
        if self == ChargingPowerSource.NoCharing:
            return 0
        if self == ChargingPowerSource.SolarOnly:
            return max(0, pv_power - total_load)
        if self == ChargingPowerSource.MinPlusSolar:
            return max(c.min_power * c.charge_efficiency_factor, pv_power - total_load)
        if self == ChargingPowerSource.MinBatteryLoad:
            if battery_strategy == BatteryLoadStrategy.PeakShaving:
                battery_strategy = BatteryLoadStrategy.PeakShavingMininal
            return max(0, pv_power - total_load - battery_load + battery_strategy.max_charing_power_with_grid(c))
        if self == ChargingPowerSource.Full:
            return max(0, pv_power - total_load + battery_strategy.max_charing_power_with_grid(c))

    def get_max_power_state_topic(self, c: Config):
        def get_topic(comp_name: str) -> str:
            return c.mqtt_discovery['components'][comp_name]['state_topic']
        if self == ChargingPowerSource.NoCharing:
            return None
        if self == ChargingPowerSource.SolarOnly:
            return get_topic('max_power_solar')
        if self == ChargingPowerSource.MinPlusSolar:
            return get_topic('max_power_min_solar')
        if self == ChargingPowerSource.MinBatteryLoad:
            return get_topic('max_power_min_battery')
        if self == ChargingPowerSource.Full:
            return get_topic('max_power_full')

def get_nightly_time(c: Config) -> tuple[bool, int]:
    """
    Return if it is night and the time to morning
    """
    time_local = time.localtime()
    time_now = time_local.tm_hour * 3600 + time_local.tm_min * 60 + time_local.tm_sec

    # If it is not night, do not charge
    if time_now > c.nightly_end and time_now < c.nightly_start:
        return False, 0
    
    # Add a day if it is before the start time
    time_now = time_now + 24 * 3600 if time_now < c.nightly_start else time_now
    if c.nightly_end < c.nightly_start:
        time_end = c.nightly_end + 24 * 3600

    # Calculate the time to morning
    remaining_time_s = time_end - time_now
    return True, remaining_time_s

class ChargingPlan(enum.Enum):
    Manual = 'Manual' # Charging not managed by the controller
    SolarOnly = 'Solar only' # Only as much as possible when there is solar power
    MinPlusSolar = 'Min + Solar' # Charge with min power (grid) plus solar
    Nightly = 'Nightly' # Charge only at night
    SolarPlusNightly = 'Solar + Nightly' # Charge with solar and at night
    MinBatteryLoad = 'Min battery load' # Charge with grid and minimal battery usage
    MaxSpeed = 'Max speed' # Charge as fast as possible

    def get_power_source(self):
        if self == ChargingPlan.Manual:
            return ChargingPowerSource.Full
        if self == ChargingPlan.SolarOnly:
            return ChargingPowerSource.SolarOnly
        if self == ChargingPlan.MinPlusSolar:
            return ChargingPowerSource.MinPlusSolar
        if self == ChargingPlan.Nightly:
            return ChargingPowerSource.Full
        if self == ChargingPlan.SolarPlusNightly:
            is_night, _ = get_nightly_time(c)
            return ChargingPowerSource.Full if is_night else ChargingPowerSource.SolarOnly
        if self == ChargingPlan.MinBatteryLoad:
            return ChargingPowerSource.MinBatteryLoad
        if self == ChargingPlan.MaxSpeed:
            return ChargingPowerSource.Full
        return ChargingPowerSource.NoCharing

    def get_amps_state_topic(self, c: Config) -> str:
        def get_topic(comp_name: str) -> str:
            return c.mqtt_discovery['components'][comp_name]['state_topic']
        if self == ChargingPlan.Manual:
            return None
        if self == ChargingPlan.SolarOnly:
            return get_topic('plan_amps_solar_only')
        if self == ChargingPlan.MinPlusSolar:
            return get_topic('plan_amps_min_solar')
        if self == ChargingPlan.Nightly:
            return get_topic('plan_amps_nightly')
        if self == ChargingPlan.MinBatteryLoad:
            return get_topic('plan_amps_min_battery')
        if self == ChargingPlan.MaxSpeed:
            return get_topic('plan_amps_max_speed')

    
class HomeassistantApi:
    def __init__(self, c: Config):
        self.c = c
        self.url = c.api_url
        self.token = c.api_token
        self.client = httpx.Client()

    def action(self, domain: str, service: str, service_data: dict) -> str:
        """
        Call a service
        """
        # return {} # TODO: Remove no-op
        url = f'{self.url}/api/services/{domain}/{service}'
        headers = {
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json',
        }
        response = self.client.post(url, headers=headers, json=service_data)
        response.raise_for_status()
        return response.text

    def template(self, template: str) -> str:
        """
        Get the value of a template
        """
        url = f'{self.url}/api/template'
        headers = {
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json',
        }
        data = {
            'template': template,
        }
        response = self.client.post(url, headers=headers, json=data)
        response.raise_for_status()
        return response.text

class WigaunApi(HomeassistantApi):
    def __init__(self, c: Config):
        super().__init__(c)

    def set_charging(self, charging: bool) -> str:
        """
        Set the charging state
        """
        return self.action('switch', 'turn_on' if charging else 'turn_off', {'entity_id': self.c.set_charging_entity_id})
    
    def set_charging_amps(self, amps: int) -> str:
        """
        Set the charging amps
        """
        return self.action('number', 'set_value', {'entity_id': self.c.set_charging_amps_entity_id, 'value': amps})

    def set_charging_plan(self, plan: ChargingPlan) -> str:
        """
        Set the charging plan
        """
        return self.action('input_select', 'select_option', {'entity_id': self.c.charging_plan_entity_id, 'option': plan.value})

    def get_top_up_limit(self) -> int:
        """
        Get the top up limit
        """
        return int(self.template(self.c.top_up_limit_template))

    def get_charging_amps(self) -> int:
        """
        Get the charging amps
        """
        amps = self.template(self.c.charging_amps_template)
        return int(amps) if amps not in ['unavailable', 'unknown'] else 0

    def get_charging_limit(self) -> int:
        """
        Get the charging limit
        """
        limit = self.template(self.c.charging_limit_template)
        return int(limit) if limit not in ['unavailable', 'unknown'] else 0

    def get_battery_load(self) -> float:
        """
        Get the battery load
        """
        return float(self.template(self.c.battery_load_template))

    def get_charging_plan(self) -> ChargingPlan:
        """
        Get the charging plan
        """
        try:
            return ChargingPlan(self.template(self.c.charging_plan_template))
        except KeyError:
            log.warning('Unknown charging plan')
            return ChargingPlan.Manual
    
    def get_car_soc(self) -> float:
        """
        Get the car state of charge
        """
        soc = self.template(self.c.car_soc_template)
        return float(soc) if soc not in ['unavailable', 'unknown'] else math.nan

    def get_inverter_soc(self) -> float:
        """
        Get the inverter state of charge
        """
        return float(self.template(self.c.inverter_soc_template))

    def get_total_load(self) -> float:
        """
        Get the total load
        """
        return float(self.template(self.c.total_load_template))

    def get_grid_power(self) -> float:
        """
        Get grid power
        """
        return float(self.template(self.c.grid_power_template))

    def get_pv_power(self) -> float:
        """
        Get photovolatic power
        """
        return float(self.template(self.c.pv_power_template))
    
    def get_charger_connected(self) -> bool:
        """
        Get the charger connected state
        """
        return self.template(self.c.charger_connected_template) == 'on'
    
    def get_is_charging(self) -> bool:
        """
        Get the charging state
        """
        return self.template(self.c.is_charging_template) == 'on'

def calculate_charging_amps(c: Config, plan: ChargingPlan, max_power: float, current_soc: float, limit_soc: float) -> float:
    """
    Get the charging power
    """
    if current_soc >= limit_soc:
        log.info('Car is full')
        return 0

    if max_power < c.min_power * c.charge_efficiency_factor:
        log.warning(f'Not enough power to charge with {max_power}W')
        return 0

    step = c.phases * c.volts * c.charge_efficiency_factor
    max_amps = math.floor(max_power / step) # Round down to the nearest step

    if max_amps < c.min_amps:
        log.warning(f'Not enough power to charge with {max_amps}A')
        return 0

    # Solar + Nightly: Determine which plan to use
    is_night = False
    remaining_time_s = 0
    if plan == ChargingPlan.SolarPlusNightly or plan == ChargingPlan.Nightly:
        is_night, remaining_time_s = get_nightly_time(c)
        if plan == ChargingPlan.SolarPlusNightly:
            plan = ChargingPlan.Nightly if is_night else ChargingPlan.SolarOnly

    # Process the plan
    if plan == ChargingPlan.Manual:
        return 0
    if plan == ChargingPlan.SolarOnly:
        if max_amps < c.min_amps:
            log.warning('Not enough power to charge on solar only')
            return 0
        return min(max_amps, c.max_amps)
    if plan == ChargingPlan.Nightly:
        if not is_night:
            log.debug('Not night')
            return 0

        # Calculate the power needed to charge the car to the limit
        remaining_capacity_wh = c.vehicle_battery_capacity * (limit_soc - current_soc) / 100
        remaining_time_h = remaining_time_s / 3600
        if remaining_time_h == 0:
            log.debug('Not enough time to charge')
            return 0
        required_amps = remaining_capacity_wh / (remaining_time_h * c.volts * c.phases)
        log.debug(f'Remaining capacity: {remaining_capacity_wh:.2f}Wh Remaining time: {remaining_time_h:.2f}h Required amps: {required_amps:.2f}A')

        amps_plan = math.ceil(required_amps)

        if amps_plan < c.min_amps:
            log.debug('Charging with min amps')
            return c.min_amps

        if amps_plan > max_amps:
            log.warning('Not enough power to reach plan')
            return max_amps

        return amps_plan
    if plan in [ChargingPlan.MinPlusSolar, ChargingPlan.MinBatteryLoad, ChargingPlan.MaxSpeed]:
        return min(max_amps, c.max_amps)

    return 0

def start_mqtt(c: Config):
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, 'kcc')
    client.username_pw_set(c.mqtt_user, c.mqtt_pass)
    def on_connect(client, userdata, flags, reason_code, properties):
        log.info(f'Connected to MQTT with result code {reason_code}')
        if reason_code != 0:
            log.fatal('Could not connect to MQTT')
            return
        reset_copy = json.loads(json.dumps(c.mqtt_discovery))
        for comp_name in reset_copy['components']:
            # Keep only the platform key
            comp = reset_copy['components'][comp_name]
            reset_copy['components'][comp_name] = {k: comp[k] for k in comp if k == 'platform'}

        log.debug(f'Publishing discovery message: {json.dumps(c.mqtt_discovery)}')
        # client.publish(c.mqtt_discovery_topic, json.dumps(reset_copy), retain=True)
        client.publish(c.mqtt_discovery_topic, json.dumps(c.mqtt_discovery), retain=True)          
        client.publish(c.mqtt_availability_topic, 'online', retain=True)
    
    client.on_connect = on_connect
    client.will_set(c.mqtt_availability_topic, 'offline', retain=True)
    client.connect(c.mqtt_host, c.mqtt_port)
    client.loop_start()
    return client

if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s:%(name)s:%(message)s')
    logging.getLogger('httpcore.http11').setLevel(logging.WARNING)
    if log.getEffectiveLevel() == logging.DEBUG:
        logging.getLogger('httpx').setLevel(logging.INFO)
    else:
        logging.getLogger('httpx').setLevel(logging.WARNING)

    log.info("Starting charger controller")
    c = Config()
    api = WigaunApi(c)
    min_charge_power = c.min_power * c.charge_efficiency_factor
    mqtt_client = start_mqtt(c)

    # Remember the state of the charger to detect when
    # to switch to manual mode
    was_manual = api.get_charging_plan() == ChargingPlan.Manual
    remembered_charging_enabled = api.get_is_charging()
    remembered_charging_amps = api.get_charging_amps()

    while True:
        # Get all the data
        try:
            charging_amps = api.get_charging_amps()
            charging_limit = api.get_charging_limit()
            charging_plan = api.get_charging_plan()
            top_up_limit = api.get_top_up_limit()
            inverter_soc = api.get_inverter_soc()
            car_soc = api.get_car_soc()
            battery_load = api.get_battery_load()
            total_load = api.get_total_load()
            grid_power = api.get_grid_power()
            pv_power = api.get_pv_power()
            charger_connected = api.get_charger_connected()
            charging = api.get_is_charging()
        except httpx.HTTPError as e:
            log.error(f'Error getting data: {e}')
            time.sleep(c.poll_interval)
            continue
        bat_strategy = BatteryLoadStrategy.from_soc(inverter_soc, c)

        # Print the data
        log.debug(f'Charging amps: {charging_amps}A')
        log.debug(f'Charging limit: {charging_limit}%')
        log.debug(f'Charging plan: {charging_plan}')
        log.debug(f'Top up limit: {top_up_limit}%')
        log.debug(f'Inverter SOC: {inverter_soc}%')
        log.debug(f'Car SOC: {car_soc}%')
        log.debug(f'Battery load: {battery_load}w')
        log.debug(f'Total Load: {total_load}w')
        log.debug(f'Grid Power: {grid_power}w')
        log.debug(f'PV Power: {pv_power}w')
        log.debug(f'Charger Connected: {charger_connected}')
        log.debug(f'Charging: {charging}')
        log.debug(f'Battery usage strategy: {bat_strategy}')

        power_sources = {ps: ps.get_max_power(c, pv_power, total_load, battery_load, bat_strategy) for ps in ChargingPowerSource}
        for ps, ps_max_power in power_sources.items():
            log.debug(f'Max power with {ps.name}: {ps_max_power}w')

        if not charger_connected:
            log.debug('Charger not connected')
            time.sleep(c.poll_interval)
            continue

        if charging_plan == ChargingPlan.Manual:
            log.debug('Charging plan is manual')
            time.sleep(c.poll_interval)
            was_manual = True
            continue

        if remembered_charging_enabled != charging or remembered_charging_amps != charging_amps and not was_manual:
            log.info('Switching to manual mode')
            remembered_charging_enabled = charging
            remembered_charging_amps = charging_amps
            api.set_charging_plan(ChargingPlan.Manual)
            time.sleep(c.poll_interval)
            was_manual = True
            continue

        was_manual = False
        plans = [ChargingPlan.SolarOnly, ChargingPlan.MinPlusSolar, ChargingPlan.Nightly, ChargingPlan.SolarPlusNightly, ChargingPlan.MinBatteryLoad, ChargingPlan.MaxSpeed]
        target_charging_amps = 0
        for plan in plans:
            target_amps = calculate_charging_amps(c, plan, power_sources[plan.get_power_source()], car_soc, charging_limit)
            target_power = target_amps * c.volts * c.phases * c.charge_efficiency_factor
            log.debug(f'Calculated charging amps for {plan.name}: {target_amps}A -> {target_power}W')
            log.debug(f'Probable load for {plan.name}: {total_load + target_power}W')
            if plan == charging_plan:
                target_charging_amps = target_amps

        log.debug(f'Target charging amps: {target_charging_amps}A -> {target_charging_amps * c.volts * c.phases * c.charge_efficiency_factor}W')

        if target_charging_amps == 0:
            if charging:
                log.info('Stop charging because of charging plan')
                api.set_charging(False)
                remembered_charging_enabled = False
                time.sleep(c.poll_interval)
                continue
            else:
                log.info('No charging needed')
                time.sleep(c.poll_interval)
                continue
        else:
            if not charging:
                if car_soc > top_up_limit:
                    log.debug('Will not charge because car is full enough')
                    time.sleep(c.poll_interval)
                    continue
                log.info('Start charging because of charging plan')
                api.set_charging_amps(target_charging_amps)
                api.set_charging(True)
                remembered_charging_enabled = True
                remembered_charging_amps = target_charging_amps
                time.sleep(c.poll_interval)
                continue
            else:
                if target_charging_amps != charging_amps:
                    log.info(f'Set charging amps to {target_charging_amps}A')
                    api.set_charging_amps(target_charging_amps)
                    remembered_charging_amps = target_charging_amps
        time.sleep(c.poll_interval)
