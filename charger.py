import enum
import math
import time
import traceback
import httpx
from config import Config
import logging
import metrics
from typing import Optional

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

def is_scheduled_charging_time(c: Config) -> bool:
    """
    Check if it is the scheduled charging time
    """
    time_local = time.localtime()
    time_now = time_local.tm_hour * 3600 + time_local.tm_min * 60 + time_local.tm_sec
    
    time_start = c.tesla_schedule_start
    time_end = time_start + 6 * 3600 # Scheduled charging start is active for 6 hours
    
    # Handle time window wrapping around midnight
    if time_end > 24 * 3600:
        # If window wraps around midnight, check if current time is before adjusted end time
        # or after start time
        time_end = time_end % (24 * 3600)
        if time_start <= time_now or time_now <= time_end:
            return True
    else:
        # Normal case - check if current time is within window
        if time_start <= time_now <= time_end:
            return True
    
    return False

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

class NightlyChargingState:
    def __init__(self):
        self.last_calc_time: float = 0
        self.cached_amps: Optional[float] = None
        
    def should_recalculate(self, recalc_interval: int) -> bool:
        return time.time() - self.last_calc_time > recalc_interval
    
    def update(self, amps: float):
        self.last_calc_time = time.time()
        self.cached_amps = amps
    
    def reset(self):
        self.last_calc_time = 0
        self.cached_amps = None

nightly_state = NightlyChargingState()

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
            nightly_state.reset()  # Reset state when not night
            return 0
        
        # Car is still not charged, switch to max speed to continue charging
        if remaining_time_s < 1.2 * c.poll_interval:
            api.set_charging_plan(ChargingPlan.MaxSpeed)
            log.info('Switching to max speed to finish charging')
            nightly_state.reset()  # Reset state when switching to max speed
            return max_amps

        # Use cached amps if available and not time to recalculate
        if not nightly_state.should_recalculate(c.nightly_recalc_interval) and nightly_state.cached_amps is not None:
            target_amps = nightly_state.cached_amps
            # Still respect the current max_power limit
            if target_amps > max_amps:
                log.warning('Not enough power to maintain cached amps')
                return max_amps
            log.debug(f'Using cached amps: {target_amps:.2f}A')
            return target_amps
        
        # Calculate the power needed to charge the car to the limit
        remaining_capacity_wh = c.vehicle_battery_capacity * (limit_soc - current_soc) / 100
        remaining_time_h = remaining_time_s / 3600
        required_amps = remaining_capacity_wh / (remaining_time_h * c.volts * c.phases)
        log.debug(f'Remaining capacity: {remaining_capacity_wh:.2f}Wh Remaining time: {remaining_time_h:.2f}h Required amps: {required_amps:.2f}A')

        amps_plan = math.ceil(required_amps)

        if amps_plan < c.min_amps:
            log.debug('Charging with min amps')
            amps_plan = c.min_amps

        if amps_plan > max_amps:
            log.warning('Not enough power to reach plan')
            amps_plan = max_amps
            
        nightly_state.update(amps_plan)  # Cache the calculated amps
        return amps_plan

    # Reset nightly state when not in nightly mode
    nightly_state.reset()
    
    # Remaining plans - use the maximum amps that its power source can provide
    if plan in [ChargingPlan.Manual, ChargingPlan.SolarOnly, ChargingPlan.MinPlusSolar, ChargingPlan.MinBatteryLoad, ChargingPlan.MaxSpeed]:
        return min(max_amps, c.max_amps)

    log.warning('Unknown charging plan')
    return 0

class UnexpectedChangeResult(enum.Enum):
    Expected = 'Expected'  # Change was expected, continue normal operation
    Disconnected = 'Disconnected'  # Charger was disconnected, go back to polling
    Scheduled = 'Scheduled'  # Change was due to scheduled charging, continue normal operation
    Ignored = 'Ignored'  # Change can be ignored, continue normal operation
    Manual = 'Manual'  # Unexpected change, switch to manual mode

def handle_unexpected_charging_change(c: Config, charging: bool, charging_amps: int, 
                                   remembered_charging_enabled: bool, remembered_charging_amps: int) -> UnexpectedChangeResult:
    """
    Handle unexpected charging changes.
    Returns the result indicating what action should be taken.
    """
    if remembered_charging_enabled == charging and remembered_charging_amps == charging_amps:
        return UnexpectedChangeResult.Expected

    charging_changed = remembered_charging_enabled != charging
    charging_amps_changed = remembered_charging_amps != charging_amps
    log.debug('Values have changed unexpectedly')
    log.debug(f'Charging enabled: {remembered_charging_enabled}->{charging}')
    log.debug(f'Charging amps: {remembered_charging_amps}->{charging_amps}')

    # Charging stopped unexpectedly, wait to see if the charger will be disconnected
    if charging_changed and not charging:
        log.debug('Waiting to see if the charger will be disconnected')
        time.sleep(c.poll_interval)            
        if not api.get_charger_connected():
            log.info('Charger disconnected')
            return UnexpectedChangeResult.Disconnected

    # Check if unexpected charging start was due to scheduled charging
    if charging_changed and charging and is_scheduled_charging_time(c):
        log.info('Charging started during scheduled charging time')
        return UnexpectedChangeResult.Scheduled

    # Dont care about the charging amps if the charger is not charging
    if charging_amps_changed and not charging:
        log.debug('Charge amps changed while not charging, ignoring')
        return UnexpectedChangeResult.Ignored

    # Something unexpected happened, switch to manual mode
    return UnexpectedChangeResult.Manual

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

        target_power_factor = c.volts * c.phases * c.charge_efficiency_factor
        all_charging_amps = {}
        for plan in [p for p in ChargingPlan]:
            target_amps = calculate_charging_amps(c, plan, power_sources[plan.get_power_source()], car_soc, charging_limit)
            target_power = target_amps * target_power_factor
            log.debug(f'Calculated charging amps for {plan.name}: {target_amps}A -> {target_power}W')
            log.debug(f'Probable load for {plan.name}: {total_load + target_power}W')
            all_charging_amps[plan] = target_amps

        target_charging_amps = all_charging_amps[charging_plan]
        target_charging_power = target_charging_amps * target_power_factor
        log.debug(f'Target charging amps: {target_charging_amps}A -> {target_charging_power}W')

        # (0). Save metrics
        metrics.save_charger_metrics(metrics_db, {
            'charging_amps': charging_amps,
            'charging_limit': charging_limit,
            'charging_plan': charging_plan.value,
            'top_up_limit': top_up_limit,
            'inverter_soc': inverter_soc,
            'car_soc': car_soc,
            'battery_load': battery_load,
            'total_load': total_load,
            'grid_power': grid_power,
            'pv_power': pv_power,
            'charger_connected': charger_connected,
            'charging': charging,
            'usage_strategy': bat_strategy.value,
            'max_power_no_charging': power_sources[ChargingPowerSource.NoCharing],
            'max_power_solar_only': power_sources[ChargingPowerSource.SolarOnly],
            'max_power_min_plus_solar': power_sources[ChargingPowerSource.MinPlusSolar],
            'max_power_min_bat_load': power_sources[ChargingPowerSource.MinBatteryLoad],
            'max_power_full': power_sources[ChargingPowerSource.Full],
            'plan_manual_amps': all_charging_amps[ChargingPlan.Manual],
            'plan_manual_power': all_charging_amps[ChargingPlan.Manual] * target_power_factor,
            'plan_solar_only_amps': all_charging_amps[ChargingPlan.SolarOnly],
            'plan_solar_only_power': all_charging_amps[ChargingPlan.SolarOnly] * target_power_factor,
            'plan_min_plus_solar_amps': all_charging_amps[ChargingPlan.MinPlusSolar],
            'plan_min_plus_solar_power': all_charging_amps[ChargingPlan.MinPlusSolar] * target_power_factor,
            'plan_nightly_amps': all_charging_amps[ChargingPlan.Nightly],
            'plan_nightly_power': all_charging_amps[ChargingPlan.Nightly] * target_power_factor,
            'plan_solar_plus_nightly_amps': all_charging_amps[ChargingPlan.SolarPlusNightly],
            'plan_solar_plus_nightly_power': all_charging_amps[ChargingPlan.SolarPlusNightly] * target_power_factor,
            'plan_min_battery_load_amps': all_charging_amps[ChargingPlan.MinBatteryLoad],
            'plan_min_battery_load_power': all_charging_amps[ChargingPlan.MinBatteryLoad] * target_power_factor,
            'plan_max_speed_amps': all_charging_amps[ChargingPlan.MaxSpeed],
            'plan_max_speed_power': all_charging_amps[ChargingPlan.MaxSpeed] * target_power_factor,
            'target_charging_amps': target_charging_amps,
            'target_charging_power': target_charging_power,
        })


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

        # 3. If values have changed unexpectedly, handle the change
        if not was_manual:
            result = handle_unexpected_charging_change(
                c, api, charging, charging_amps, remembered_charging_enabled, remembered_charging_amps)
            
            remembered_charging_enabled = charging
            remembered_charging_amps = charging_amps
            
            # Handle the different results
            if result == UnexpectedChangeResult.Disconnected:
                time.sleep(c.poll_interval)
                continue
            elif result == UnexpectedChangeResult.Manual:
                log.info('Switching to manual mode')
                api.set_charging_plan(ChargingPlan.Manual)
                api.notification('KEVin', 'Nastavljeno na ročno polnjenje')
                was_manual = True
                time.sleep(c.poll_interval)
                continue
            elif result == UnexpectedChangeResult.Scheduled:
                log.info('Charging started during scheduled charging time')
                api.notification('KEVin', 'Začetek polnjenja')

            # Expected, Scheduled and Ignored cases continue normal operation

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
    metrics_db = metrics.get_db_connection()
    if not metrics:
        log.error('Failed to connect to metrics database')
        exit(1)
    if not metrics.create_charger_metrics_table(metrics_db):
        log.error('Failed to create charger metrics table')
        exit(1)
    
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
        finally:
            metrics_db.close()
            api.client.close()
            log.info('Stopped charger controller')
            break