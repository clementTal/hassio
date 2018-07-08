"""
Support for Chinese wifi thermostats (Floureon, Beok, Beca Energy)
configuration.yaml
climate:
  - platform: broadlink
    name: xxx
    mac: xxxx
    host: xxxx
"""

import logging, binascii, json, pytz
import voluptuous as vol
from socket import timeout
from datetime import timedelta

_LOGGER = logging.getLogger(__name__)


from homeassistant.components.climate import ClimateDevice, SUPPORT_TARGET_TEMPERATURE, SUPPORT_ON_OFF, PLATFORM_SCHEMA, SUPPORT_OPERATION_MODE, DEFAULT_MIN_TEMP, DEFAULT_MAX_TEMP, STATE_AUTO
from homeassistant.const import TEMP_CELSIUS, ATTR_TEMPERATURE, CONF_HOST, CONF_MAC, CONF_NAME, STATE_ON, STATE_OFF
from homeassistant.helpers.discovery import load_platform
import homeassistant.helpers.config_validation as cv

DOMAIN = 'broadlink'
REQUIREMENTS = ['broadlink==0.9.0']
DEPENDENCIES = []

POWER_ON = 1
POWER_OFF = 0
AUTO = 1
MANUAL = 0

CONF_MODE_LIST = 'modes'
CONF_MIN_TEMP = 'min_temp'
CONF_MAX_TEMP = 'max_temp'

# 1 | SEN | Sensor control option | 0:internal sensor 1:external sensor 2:internal control temperature, external limit temperature | 0:internal sensor
# 2 | OSV | Limit temperature value of external sensor | 5-99C | 42C
# 3 | dIF | Return difference of limit temperature value of external sensor | 1-9C | 2C
# 4 | SVH | Set upper limit temperature value | 5-99C | 35C
# 5 | SVL | Set lower limit temperature value | 5-99C | 5C
# 6 | AdJ | Measure temperature | Measure temperature,check and calibration | 0.1C precision Calibration (actual temperature)
# 7 | FrE | Anti-freezing function | 00:anti-freezing function shut down 01:anti-freezing function open | 00:anti-freezing function shut down
# 8 | POn | Power on memory | 00:Power on no need memory 01:Power on need memory | 00:Power on no need memory
# loop_mode refers to index in [ "12345,67", "123456,7", "1234567" ]
  # E.g. loop_mode = 0 ("12345,67") means Saturday and Sunday follow the "weekend" schedule
  # loop = 2 ("1234567") means every day (including Saturday and Sunday) follows the "weekday" schedule
# schedule_week_day is a list (ordered) of 6 dicts like:
  # {'start_hour':17, 'start_minute':30, 'temp': 22 }
  # Each one specifies the thermostat temp that will become effective at start_hour:start_minute
# schedule_week_end is similar but only has 2 (e.g. switch on in morning and off in afternoon)
CONF_ADVANCED_CONFIG = 'advanced_config'
CONF_SCHEDULE_WEEKDAY = 'schedule_week_day'
CONF_SCHEDULE_WEEKEND = 'schedule_week_end'
CONF_WEEKDAY = "weekday"
CONF_WEEKEND = "weekend"

# Validation of the user's configuration
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_HOST): cv.string,
    vol.Required(CONF_MAC): cv.string,
    vol.Required(CONF_NAME): cv.string,
    vol.Optional(CONF_MIN_TEMP, default=5): cv.positive_int,
    vol.Optional(CONF_MAX_TEMP, default=35): cv.positive_int,
    vol.Optional(CONF_ADVANCED_CONFIG, default='{"loop_mode": "0", "sen": "0", "osv": "42", "dif": "2", "svh": "35", "svl": "5", "adj": "0", "fre": "01", "pon": "00"}'): cv.string,
    vol.Optional(CONF_SCHEDULE_WEEKDAY, default='[{"start_hour":"06", "start_minute":"30", "temp":"20"}, {"start_hour":"09", "start_minute":"00", "temp":"17"}, {"start_hour":"12", "start_minute":"00", "temp":"20" }, {"start_hour":"14", "start_minute":"00", "temp":"17"}, {"start_hour":"18", "start_minute":"00", "temp":"20" }, {"start_hour":22, "start_minute":30, "temp":17}]'): cv.string,
    vol.Optional(CONF_SCHEDULE_WEEKEND, default='[{"start_hour":"08", "start_minute":"30", "temp":"20"}, {"start_hour":"23", "start_minute":"00", "temp":"17"}]'): cv.string
})

SET_SCHEDULE_SCHEMA = vol.Schema({
    vol.Required(CONF_WEEKDAY, default='[{"start_hour":"06", "start_minute":"30", "temp":"20"}, {"start_hour":"09", "start_minute":"00", "temp":"17"}, {"start_hour":"12", "start_minute":"00", "temp":"20" }, {"start_hour":"14", "start_minute":"00", "temp":"17"}, {"start_hour":"18", "start_minute":"00", "temp":"20" }, {"start_hour":22, "start_minute":30, "temp":17}]'): cv.string,
    vol.Required(CONF_WEEKEND, default='[{"start_hour":"08", "start_minute":"30", "temp":"20"}, {"start_hour":"23", "start_minute":"00", "temp":"17"}]'): cv.string
})

SET_ADVANCED_CONF_SCHEMA = vol.Schema({
      vol.Required(CONF_ADVANCED_CONFIG, default='{"loop_mode": "0", "sen": "0", "osv": "42", "dif": "2", "svh": "35", "svl": "5", "adj": "0", "fre": "01", "pon": "00"}'): cv.string,
})

def setup_platform(hass, config, add_devices, discovery_info=None):
  _LOGGER.debug("Adding component: wifi_thermostat ...")

  mac_addr = config.get(CONF_MAC)
  ip_addr = config.get(CONF_HOST)
  name = config.get(CONF_NAME)
  _LOGGER.info("["+DOMAIN+"] mac: "+mac_addr+" | ip_addr: "+ip_addr+" | name: "+name)
      
  if mac_addr is None:
    _LOGGER.error("Wifi Thermostat: Invalid mac_addr !")
    return False
  
  if ip_addr is None:
    _LOGGER.error("Wifi Thermostat: Invalid ip_addr !")
    return False
       
  if name is None:
    _LOGGER.error("Wifi Thermostat: Invalid name !")
    return False

  wt = wifi_thermostat(
    mac_addr, 
    ip_addr, 
    name, 
    config.get(CONF_ADVANCED_CONFIG),
    config.get(CONF_SCHEDULE_WEEKDAY),
    config.get(CONF_SCHEDULE_WEEKDAY),
    config.get(CONF_MIN_TEMP),
    config.get(CONF_MAX_TEMP)
    )

  add_devices([WifiThermostat(hass, wt)])
  
  def handle_set_schedule(service):
    wt = wifi_thermostat(
      config.get(CONF_MAC), 
      config.get(CONF_HOST), 
      config.get(CONF_NAME), 
      config.get(CONF_ADVANCED_CONFIG),
      config.get(CONF_SCHEDULE_WEEKDAY),
      config.get(CONF_SCHEDULE_WEEKDAY),
      config.get(CONF_MIN_TEMP),
      config.get(CONF_MAX_TEMP)
    )
    schedule_wd = service.data.get(CONF_SCHEDULE_WEEKDAY)
    schedule_we = service.data.get(CONF_SCHEDULE_WEEKEDN)
    wt.set_schedule({CONF_WEEKDAY: json.loads(schedule_wd.replace("'", '"'), cls=Decoder), CONF_WEEKEND: json.loads(schedule_we.replace("'", '"'), cls=Decoder)})
  
  hass.services.register(DOMAIN, 'set_schedule', handle_set_schedule, schema=SET_SCHEDULE_SCHEMA)
  
  def handle_set_advanced_conf(service):
    wt = wifi_thermostat(
      config.get(CONF_MAC), 
      config.get(CONF_HOST), 
      config.get(CONF_NAME), 
      config.get(CONF_ADVANCED_CONFIG),
      config.get(CONF_SCHEDULE_WEEKDAY),
      config.get(CONF_SCHEDULE_WEEKDAY),
      config.get(CONF_MIN_TEMP),
      config.get(CONF_MAX_TEMP)
    )
    advanced_conf = service.data.get(CONF_ADVANCED_CONFIG)
    wt.set_advanced_config(json.loads(advanced_conf.replace("'", '"'), cls=Decoder))
  
  hass.services.register(DOMAIN, 'set_advanced_conf', handle_set_advanced_conf, schema=SET_ADVANCED_CONF_SCHEMA)

  _LOGGER.debug("Wifi Thermostat: Component successfully added !")
  return True

class Decoder(json.JSONDecoder):
  def decode(self, s):
    result = super(Decoder, self).decode(s)
    return self._decode(result)
  def _decode(self, o):
    if isinstance(o, str):
      try:
        return int(o)
      except ValueError:
        try:
          return float(o)
        except ValueError:
          return o
    elif isinstance(o, dict):
      return {k: self._decode(v) for k, v in o.items()}
    elif isinstance(o, list):
      return [self._decode(v) for v in o]
    else:
      return o

class wifi_thermostat:
  def __init__(self, mac, ip, name, advanced_config, schedule_wd, schedule_we, min_temp, max_temp):
    self.HOST = ip
    self.PORT = 80
    self.MAC = bytes.fromhex(''.join(reversed(mac.split(':'))))
    self.current_temp = None
    self.current_operation = None
    self.power = None
    self.target_temperature = None
    self.name = name
    self.loop_mode = json.loads(advanced_config, cls=Decoder)["loop_mode"]
    self.operation_list = [STATE_AUTO, STATE_OFF, STATE_ON]
    self.min_temp = min_temp
    self.max_temp = max_temp
    self.state = 0
    self.advanced_config = json.loads(advanced_config, cls=Decoder)
    self.schedule = {CONF_WEEKDAY: json.loads(schedule_wd, cls=Decoder), CONF_WEEKEND: json.loads(schedule_we, cls=Decoder)}
    self.set_advanced_config(self.advanced_config)
    self.set_schedule(self.schedule)
      
  def set_advanced_config(self, advanced_config):
    try:
      device = self.connect()
      if device.auth():
        device.set_advanced(advanced_config["loop_mode"], 
          advanced_config["sen"],
          advanced_config["osv"], 
          advanced_config["dif"],
          advanced_config["svh"],
          advanced_config["svl"],
          advanced_config["adj"],
          advanced_config["fre"],
          advanced_config["pon"]
        )
    except timeout:
      _LOGGER.debug("read_status timeout")

  def set_schedule(self, schedule):
    try:
      device = self.connect()
      if device.auth():  
        device.set_schedule(schedule[CONF_WEEKDAY], schedule[CONF_WEEKEND])
    except timeout:
      _LOGGER.debug("read_status timeout")

  def poweronoff(self, power):
    try:
      device = self.connect()
      if device.auth():
        if(str(power) == STATE_OFF):
          device.set_power(POWER_OFF)
        else:
          device.set_power(POWER_ON)
    except timeout:
      _LOGGER.debug("read_status timeout")

  def set_temperature(self, temperature):
    try:
      device = self.connect()
      if device.auth():
        device.set_temp(float(temperature))
    except timeout:
      _LOGGER.debug("read_status timeout")

  def set_operation_mode(self, mode):
    try:
      device = self.connect()
      if device.auth():
        if mode == STATE_AUTO:
          device.set_power(POWER_ON)
          device.set_mode(AUTO, self.loop_mode)
        elif mode == STATE_ON:
          device.set_power(POWER_ON)
          device.set_mode(MANUAL, self.loop_mode) 
        elif mode == STATE_OFF:
          device.set_power(POWER_OFF)
          device.set_mode(AUTO, self.loop_mode)
    except timeout:
      _LOGGER.debug("read_status timeout")

  def read_status(self):
    try:
      device = self.connect()
      if device.auth():
        data = device.get_full_status()
        json_data = json.loads(json.dumps(data))
        self.current_temp = json_data['room_temp']
        self.target_temperature = json_data['thermostat_temp']
        self.current_operation = STATE_OFF if json_data["power"] == 0 else (STATE_AUTO if json_data["auto_mode"] == 1 else STATE_ON)
        self.state = STATE_ON if json_data["active"] == 0 else STATE_OFF
    except timeout:
      _LOGGER.debug("read_status timeout")
      
  def connect(self):
    import broadlink
    return broadlink.gendevice(0x4EAD, (self.HOST, self.PORT), self.MAC)

class WifiThermostat(ClimateDevice):
  def __init__(self, hass, device):
    self._device = device
    self._hass = hass

  @property
  def supported_features(self):
    return (SUPPORT_TARGET_TEMPERATURE | SUPPORT_OPERATION_MODE)

  @property
  def should_poll(self):
    return True

  @property
  def state(self):
    return self._device.state
    
  @property
  def name(self):
    return self._device.name

  @property
  def temperature_unit(self):
    return TEMP_CELSIUS
  
  @property
  def min_temp(self):
    return self._device.min_temp
  
  @property
  def max_temp(self):
    return self._device.max_temp

  @property
  def current_temperature(self):
    return self._device.current_temp

  @property
  def target_temperature(self):
    return self._device.target_temperature

  @property
  def operation_list(self):
    """Return the list of available operation modes."""
    return self._device.operation_list
   
  @property
  def current_operation(self):
    """Return current operation ie. heat, cool, idle."""
    return self._device.current_operation
    
  @property
  def advanced_config(self):
    return self._device.advanced_config
    
  @property
  def schedule(self):
    return self._device.schedule

  def set_advance_config(self, config_json):
    self._device.set_advanced_config(json.loads(config_json, cls=Decoder))
  
  def set_schedule(self, schedule_json):
    self._device.set_schedule(json.loads(schedule_json, cls=Decoder))

  def set_temperature(self, **kwargs):
    if kwargs.get(ATTR_TEMPERATURE) is not None:
      self._device.set_temperature(kwargs.get(ATTR_TEMPERATURE))

  def turn_on(self):
    self._device.poweronoff(STATE_ON)

  def turn_off(self):
    self._device.poweronoff(STATE_OFF)
    
  def set_operation_mode(self, operation_mode):
    self._device.set_operation_mode(operation_mode)

  def update(self):
    self._device.read_status()

