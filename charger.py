import enum
import math
import time
import traceback
import httpx
from config import Config
import logging

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
    
    def notification(self, title: str, message: str):
        """
        Send a notification
        """
        return self.action('notify', 'notify', {'message': message, 'title': title})

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
        return int(float(self.template(self.c.top_up_limit_template)))

    def get_charging_amps(self) -> int:
        """
        Get the charging amps
        """
        try:
            return int(self.template(self.c.charging_amps_template))
        except ValueError:
            log.debug('Charging amps not available')
            return 0

    def get_charging_limit(self) -> int:
        """
        Get the charging limit
        """
        return int(self.template(self.c.charging_limit_template))

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
        return float(self.template(self.c.car_soc_template))

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
    if math.isnan(current_soc):
        log.warning('Car state of charge is not available')
        return 0

    if current_soc >= limit_soc:
        log.info('Car is full')
        return 0

    # Check if there is enough power to charge
    if max_power < c.min_power * c.charge_efficiency_factor:
        log.debug(f'Not enough power to charge with {max_power}W')
        return 0

    # Round down the maximum power to the nearest amp 
    step = c.phases * c.volts * c.charge_efficiency_factor
    max_amps = math.floor(max_power / step) # Round down to the nearest step

    # Check if there is enough power to charge with the minimum amps
    if max_amps < c.min_amps:
        log.debug(f'Not enough power to charge with {max_amps}A')
        return 0

    # Solar + Nightly: Determine which plan to use
    is_night = False
    remaining_time_s = 0
    if plan == ChargingPlan.SolarPlusNightly or plan == ChargingPlan.Nightly:
        is_night, remaining_time_s = get_nightly_time(c)
        if plan == ChargingPlan.SolarPlusNightly:
            plan = ChargingPlan.Nightly if is_night else ChargingPlan.SolarOnly

    # Process the plan    
    if plan == ChargingPlan.Nightly:
        if not is_night:
            log.debug('Not night')
            return 0
        
        # Car is still not charged, switch to max speed to continue charging
        if remaining_time_s < 1.2 * c.poll_interval:
            api.set_charging_plan(ChargingPlan.MaxSpeed)
            log.info('Switching to max speed to finish charging')
            return max_amps
        
        # Calculate the power needed to charge the car to the limit
        remaining_capacity_wh = c.vehicle_battery_capacity * (limit_soc - current_soc) / 100
        remaining_time_h = remaining_time_s / 3600
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
    # Remaining plans - use the maximum amps that its power source can provide
    if plan in [ChargingPlan.Manual, ChargingPlan.SolarOnly, ChargingPlan.MinPlusSolar, ChargingPlan.MinBatteryLoad, ChargingPlan.MaxSpeed]:
        return min(max_amps, c.max_amps)

    log.warning('Unknown charging plan')
    return 0

def main(c: Config, api: WigaunApi):
    log.info("Starting charger controller")

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
        except ValueError as e:
            log.error(f'Failed to convert data: {e}')
            log.error(traceback.format_exc())
            time.sleep(c.poll_interval)
            continue
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

        # Calculate the possible power sources
        power_sources = {ps: ps.get_max_power(c, pv_power, total_load, battery_load, bat_strategy) for ps in ChargingPowerSource}
        for ps, ps_max_power in power_sources.items():
            log.debug(f'Max power with {ps.name}: {ps_max_power}w')

        all_charging_amps = {}
        for plan in [p for p in ChargingPlan]:
            target_amps = calculate_charging_amps(c, plan, power_sources[plan.get_power_source()], car_soc, charging_limit)
            target_power = target_amps * c.volts * c.phases * c.charge_efficiency_factor
            log.debug(f'Calculated charging amps for {plan.name}: {target_amps}A -> {target_power}W')
            log.debug(f'Probable load for {plan.name}: {total_load + target_power}W')
            all_charging_amps[plan] = target_amps

        target_charging_amps = all_charging_amps[charging_plan]
        log.debug(f'Target charging amps: {target_charging_amps}A -> {target_charging_amps * c.volts * c.phases * c.charge_efficiency_factor}W')

        # 1. Check if the charger is connected before doing anything
        if not charger_connected:
            log.debug('Charger not connected')
            time.sleep(c.poll_interval)
            continue

        # 2. Check if the charger is in manual mode
        if charging_plan == ChargingPlan.Manual:
            log.debug('Charging plan is manual')
            time.sleep(c.poll_interval)
            was_manual = True
            continue

        # 3. If values have changed unexpectedly, switch to manual mode
        if remembered_charging_enabled != charging or remembered_charging_amps != charging_amps and not was_manual:
            log.debug('Values have changed unexpectedly')
            log.debug(f'Charging enabled: {remembered_charging_enabled}->{charging}')
            log.debug(f'Charging amps: {remembered_charging_amps}->{charging_amps}')

            remembered_charging_enabled = charging
            remembered_charging_amps = target_charging_amps

            if not charging:
                # Wait to see if the charger is disconnected
                log.debug('Waiting to see if the charger will be disconnected')
                time.sleep(c.poll_interval)            
                if not api.get_charger_connected():
                    log.info('Charger disconnected')
                    time.sleep(c.poll_interval)
                    continue

            log.info('Switching to manual mode')
            api.set_charging_plan(ChargingPlan.Manual)
            api.notification('KEVin', 'Nastavljeno na ročno polnjenje')
            was_manual = True
            time.sleep(c.poll_interval)
            continue

        was_manual = False

        # 4. Set the charging plan (if needed)
        if target_charging_amps == 0:
            # Stop charging
            if charging:
                log.info('Stop charging because of charging plan')
                api.set_charging(False)
                remembered_charging_enabled = False
                api.notification('KEVin', 'Polnjenje končano')
                time.sleep(c.poll_interval)
                continue
            else:
                log.debug('No charging needed')
                time.sleep(c.poll_interval)
                continue
        else:
            # Start charging
            if not charging:
                if car_soc > top_up_limit:
                    log.debug('Will not charge because car is full enough')
                    time.sleep(c.poll_interval)
                    continue
                log.info('Start charging because of charging plan')
                log.info(f'Set charging amps to {target_charging_amps}A')
                api.set_charging_amps(target_charging_amps)
                api.set_charging(True)
                remembered_charging_enabled = True
                remembered_charging_amps = target_charging_amps
                api.notification('KEVin', 'Začetek polnjenja')
                time.sleep(c.poll_interval)
                continue
            else:
                # Check if the charging amps need to be adjusted
                if target_charging_amps != charging_amps:
                    log.info(f'Set charging amps to {target_charging_amps}A')
                    api.set_charging_amps(target_charging_amps)
                    remembered_charging_amps = target_charging_amps
        time.sleep(c.poll_interval)

if __name__ == '__main__':
    c = Config()
    api = WigaunApi(c)
    # Set up logging
    logging.basicConfig(level=c.log_level, format='%(asctime)s - %(levelname)s:%(name)s:%(message)s')
    logging.getLogger('httpcore.http11').setLevel(logging.WARNING)
    if log.getEffectiveLevel() == logging.DEBUG:
        logging.getLogger('httpx').setLevel(logging.INFO)
    else:
        logging.getLogger('httpx').setLevel(logging.WARNING)
    
    # Run the main loop
    while True:
        try:
            main(c, api)
        except KeyboardInterrupt:
            log.info('Stopping charger controller')
            break
        except Exception as e:
            log.error(f'Error in charger controller: {e}')
            log.error(traceback.format_exc())
            time.sleep(60)
            log.info('Restarting charger controller')
            api.notification('KEVin', 'Krmilnik se je sesul')
            continue