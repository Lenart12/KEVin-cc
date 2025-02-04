import yaml
from datetime import datetime

class Config:
    def __init__(self, config_fn: str = 'config.yaml'):
        with open(config_fn, 'r') as f:
            config = yaml.safe_load(f)
        self.api_url = config['api']['url']
        self.api_token = config['api']['token']

        self.set_charging_entity_id = config['api']['entities']['switch_set_charging']
        self.set_charging_amps_entity_id = config['api']['entities']['number_set_charging_amps']

        self.charging_amps_template = config['api']['templates']['charging_amps']
        self.charging_limit_template = config['api']['templates']['charging_limit']
        self.charging_plan_template = config['api']['templates']['charging_plan']
        self.inverter_soc_template = config['api']['templates']['inverter_soc']
        self.car_soc_template = config['api']['templates']['car_soc']
        self.total_load_template = config['api']['templates']['total_load']
        self.grid_power_template = config['api']['templates']['grid_power']
        self.pv_power_template = config['api']['templates']['pv_power']
        self.charger_connected_template = config['api']['templates']['charger_connected']
        self.is_charging_template = config['api']['templates']['is_charging']

        self.min_amps = config['charger']['min_amps']
        self.max_amps = config['charger']['max_amps']
        self.min_power = config['charger']['min_power']
        self.max_power = config['charger']['max_power']
        self.vehicle_battery_capacity = config['charger']['vehicle_battery_capacity']
        self.phases = config['charger']['phases']
        self.volts = config['charger']['volts']
        self.poll_interval = config['charger']['poll_interval']
        self.charge_efficiency_factor = config['charger']['charge_efficiency_factor']
        def time_to_seconds(time_str):
            t = datetime.strptime(time_str, '%H:%M')
            return t.hour * 3600 + t.minute * 60
        self.nightly_start = time_to_seconds(config['charger']['nightly']['start'])
        self.nightly_end = time_to_seconds(config['charger']['nightly']['end'])

        self.battery_soc_no_charging = config['battery']['no_charging']['soc']
        self.battery_soc_reserve   = config['battery']['reserve']['soc']
        self.battery_power_reserve = config['battery']['reserve']['max_power']
        self.battery_soc_peak_shaving_minimal   = config['battery']['peak_shaving_minimal']['soc']
        self.battery_power_peak_shaving_minimal = config['battery']['peak_shaving_minimal']['max_power']
        self.battery_power_peak_shaving = config['battery']['peak_shaving']['max_power']

        self.mqtt_host = config['mqtt']['host']
        self.mqtt_port = config['mqtt']['port']
        self.mqtt_user = config['mqtt']['username']
        self.mqtt_pass = config['mqtt']['password']
        self.mqtt_discovery_topic = config['mqtt']['discovery_topic']
        self.mqtt_discovery = config['mqtt']['discovery']
        self.mqtt_availability_topic = config['mqtt']['discovery']['availability_topic']