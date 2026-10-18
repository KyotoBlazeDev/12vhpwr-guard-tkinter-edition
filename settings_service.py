"""Validate and persist protection settings before applying changes in memory."""
import configparser
import math
import os
from pathlib import Path

import hwinfo_12vhpwr_guard as guard


def current():
    threshold, seconds = guard.get_config_values()
    return dict(threshold_amps=threshold, sustained_seconds_required=seconds,
                response_mode=guard.get_response_mode(), sensor_backend=guard.SENSOR_BACKEND,
                **guard.get_tier_values())


def editable():
    result = current()
    disk = guard.load_config()
    for key in (*guard.DEFAULT_TIER_VALUES, 'sensor_backend'):
        result[key] = disk[key]
    return result


def validate(values):
    result = dict(values)
    for key in ('threshold_amps', 'sustained_seconds_required', *guard.DEFAULT_TIER_VALUES):
        value = float(result[key])
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f'{key} must be finite and positive')
        result[key] = value
    if not result['threshold_amps'] <= result['critical_amps'] <= result['catastrophic_amps'] <= 30:
        raise ValueError('Require threshold ≤ critical ≤ catastrophic ≤ 30 A (sensor plausibility ceiling).')
    if result['sensor_backend'] not in guard.VALID_SENSOR_BACKENDS:
        raise ValueError('Unknown sensor backend')
    if result['response_mode'] not in guard.VALID_RESPONSE_MODES:
        raise ValueError('Unknown response mode')
    return result


def apply(values, path=None):
    values = validate(values)
    path = Path(path or guard.CONFIG_PATH)
    config = configparser.ConfigParser()
    try:
        config.read(path, encoding='utf-8-sig')
    except configparser.Error as error:
        raise ValueError(f'Existing config.ini is invalid; repair it before saving: {error}') from error
    if not config.has_section('Settings'):
        config.add_section('Settings')
    for key, value in values.items():
        config.set('Settings', key, str(value))
    temporary = path.with_suffix('.ini.tmp')
    try:
        with temporary.open('w', encoding='utf-8') as stream:
            config.write(stream)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    guard.set_config_values(values['threshold_amps'], values['sustained_seconds_required'])
    guard.set_response_mode(values['response_mode'])
    # Advanced timings and backend selection take effect at next launch, avoiding
    # changing deadlines of a mitigation already in progress.
    return values
