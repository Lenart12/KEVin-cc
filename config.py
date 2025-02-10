import yaml
from datetime import datetime

class Config:
    def _get(self, *args):
        result = self._config
        for arg in args:
            result = result[arg]
        if isinstance(result, str):
            if result.startswith('$secret '):
                secret = result.split('$secret ')[-1]
                result = self._config['_secrets'][secret]

        return result

    def __init__(self, config_fn: str = 'config.yaml', secrets_fn: str = 'secrets.yaml'):
        with open(config_fn, 'r') as f:
            self._config = yaml.safe_load(f)
        with open(secrets_fn, 'r') as f:
            self._config['_secrets'] = yaml.safe_load(f)['secrets']

        self.log_level = self._get('charger', 'log_level')

        self.api_url = self._get('api', 'url')
        self.api_token = self._get('api', 'token')

        self.set_charging_entity_id = self._get('api', 'entities', 'switch_set_charging')
        self.set_charging_amps_entity_id = self._get('api', 'entities', 'number_set_charging_amps')
        self.charging_plan_entity_id = self._get('api', 'entities', 'input_select_charging_plan')

        self.top_up_limit_template = self._get('api', 'templates', 'top_up_limit')
        self.charging_amps_template = self._get('api', 'templates', 'charging_amps')
        self.charging_limit_template = self._get('api', 'templates', 'charging_limit')
        self.charging_plan_template = self._get('api', 'templates', 'charging_plan')
        self.inverter_soc_template = self._get('api', 'templates', 'inverter_soc')
        self.car_soc_template = self._get('api', 'templates', 'car_soc')
        self.battery_load_template = self._get('api', 'templates', 'battery_load')
        self.total_load_template = self._get('api', 'templates', 'total_load')
        self.grid_power_template = self._get('api', 'templates', 'grid_power')
        self.pv_power_template = self._get('api', 'templates', 'pv_power')
        self.charger_connected_template = self._get('api', 'templates', 'charger_connected')
        self.is_charging_template = self._get('api', 'templates', 'is_charging')

        self.min_amps = self._get('charger', 'min_amps')
        self.max_amps = self._get('charger', 'max_amps')
        self.min_power = self._get('charger', 'min_power')
        self.max_power = self._get('charger', 'max_power')
        self.vehicle_battery_capacity = self._get('charger', 'vehicle_battery_capacity')
        self.phases = self._get('charger', 'phases')
        self.volts = self._get('charger', 'volts')
        self.poll_interval = self._get('charger', 'poll_interval')
        self.charge_efficiency_factor = self._get('charger', 'charge_efficiency_factor')
        self.min_plus_solar_min_power = self._get('charger', 'min_plus_solar', 'min_power')
        def time_to_seconds(time_str):
            t = datetime.strptime(time_str, '%H:%M')
            return t.hour * 3600 + t.minute * 60
        self.nightly_start = time_to_seconds(self._get('charger', 'nightly', 'start'))
        self.nightly_end = time_to_seconds(self._get('charger', 'nightly', 'end'))
        self.nightly_recalc_interval = self._get('charger', 'nightly', 'recalc_interval')
        self.nightly_amps_offset = self._get('charger', 'nightly', 'amps_offset')
        self.tesla_schedule_start = time_to_seconds(self._get('charger', 'tesla_schedule', 'start'))

        self.battery_soc_no_charging = self._get('battery', 'no_charging', 'soc')
        self.battery_soc_reserve = self._get('battery', 'reserve', 'soc')
        self.battery_power_reserve = self._get('battery', 'reserve', 'max_power')
        self.battery_reserve_hysteresis = self._get('battery', 'reserve', 'hysteresis')
        self.battery_soc_peak_shaving_minimal = self._get('battery', 'peak_shaving_minimal', 'soc')
        self.battery_power_peak_shaving_minimal = self._get('battery', 'peak_shaving_minimal', 'max_power')
        self.battery_power_peak_shaving = self._get('battery', 'peak_shaving', 'max_power')
