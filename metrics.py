import sqlite3
import datetime

def get_db_connection():
    db_name = 'charger_metrics.db'
    try:
        conn = sqlite3.connect(db_name)
        return conn
    except sqlite3.Error as e:
        print(f"Database connection error: {e}")
        return None

def create_charger_metrics_table(conn):
    try:
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS charger_metrics (
            timestamp TEXT,
            charging_amps INTEGER,
            charging_limit INTEGER,
            charging_plan TEXT,
            top_up_limit INTEGER,
            inverter_soc REAL,
            car_soc REAL,
            battery_load REAL,
            total_load REAL,
            grid_power REAL,
            pv_power REAL,
            charger_connected BOOLEAN,
            charging BOOLEAN,
            usage_strategy TEXT,
            max_power_no_charging REAL,
            max_power_solar_only REAL,
            max_power_min_plus_solar REAL,
            max_power_min_bat_load REAL,
            max_power_full REAL,
            plan_manual_amps INTEGER,
            plan_manual_power REAL,
            plan_solar_only_amps INTEGER,
            plan_solar_only_power REAL,
            plan_min_plus_solar_amps INTEGER,
            plan_min_plus_solar_power REAL,
            plan_nightly_amps INTEGER,
            plan_nightly_power REAL,
            plan_solar_plus_nightly_amps INTEGER,
            plan_solar_plus_nightly_power REAL,
            plan_min_battery_load_amps INTEGER,
            plan_min_battery_load_power REAL,
            plan_max_speed_amps INTEGER,
            plan_max_speed_power REAL,
            target_charging_amps INTEGER,
            target_charging_power REAL
        )
        """)
        conn.commit()
        return True
    except sqlite3.Error as e:
        print(f"Database error while creating table: {e}")
        return False

def save_charger_metrics(conn, data_dict):
    try:
        cursor = conn.cursor()

        timestamp = datetime.datetime.now().isoformat() # Automatically generate timestamp

        sql = """
        INSERT INTO charger_metrics (
            timestamp,
            charging_amps,
            charging_limit,
            charging_plan,
            top_up_limit,
            inverter_soc,
            car_soc,
            battery_load,
            total_load,
            grid_power,
            pv_power,
            charger_connected,
            charging,
            usage_strategy,
            max_power_no_charging,
            max_power_solar_only,
            max_power_min_plus_solar,
            max_power_min_bat_load,
            max_power_full,
            plan_manual_amps,
            plan_manual_power,
            plan_solar_only_amps,
            plan_solar_only_power,
            plan_min_plus_solar_amps,
            plan_min_plus_solar_power,
            plan_nightly_amps,
            plan_nightly_power,
            plan_solar_plus_nightly_amps,
            plan_solar_plus_nightly_power,
            plan_min_battery_load_amps,
            plan_min_battery_load_power,
            plan_max_speed_amps,
            plan_max_speed_power,
            target_charging_amps,
            target_charging_power
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

        data_tuple = (
            timestamp,
            data_dict.get('charging_amps'),
            data_dict.get('charging_limit'),
            data_dict.get('charging_plan'),
            data_dict.get('top_up_limit'),
            data_dict.get('inverter_soc'),
            data_dict.get('car_soc'),
            data_dict.get('battery_load'),
            data_dict.get('total_load'),
            data_dict.get('grid_power'),
            data_dict.get('pv_power'),
            data_dict.get('charger_connected'),
            data_dict.get('charging'),
            data_dict.get('usage_strategy'),
            data_dict.get('max_power_no_charging'),
            data_dict.get('max_power_solar_only'),
            data_dict.get('max_power_min_plus_solar'),
            data_dict.get('max_power_min_bat_load'),
            data_dict.get('max_power_full'),
            data_dict.get('plan_manual_amps'),
            data_dict.get('plan_manual_power'),
            data_dict.get('plan_solar_only_amps'),
            data_dict.get('plan_solar_only_power'),
            data_dict.get('plan_min_plus_solar_amps'),
            data_dict.get('plan_min_plus_solar_power'),
            data_dict.get('plan_nightly_amps'),
            data_dict.get('plan_nightly_power'),
            data_dict.get('plan_solar_plus_nightly_amps'),
            data_dict.get('plan_solar_plus_nightly_power'),
            data_dict.get('plan_min_battery_load_amps'),
            data_dict.get('plan_min_battery_load_power'),
            data_dict.get('plan_max_speed_amps'),
            data_dict.get('plan_max_speed_power'),
            data_dict.get('target_charging_amps'),
            data_dict.get('target_charging_power')
        )

        cursor.execute(sql, data_tuple)
        conn.commit()
        return True

    except sqlite3.Error as e:
        print(f"Database error during insertion: {e}")
        return False
