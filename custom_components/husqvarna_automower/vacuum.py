"""Creates a vacuum entity for the mower"""
import json
import logging
from datetime import datetime

import voluptuous as vol
from aiohttp import ClientResponseError

from homeassistant.components.vacuum import (
    ATTR_STATUS,
    STATE_CLEANING,
    STATE_DOCKED,
    STATE_ERROR,
    STATE_IDLE,
    STATE_PAUSED,
    STATE_RETURNING,
    SUPPORT_BATTERY,
    SUPPORT_MAP,
    SUPPORT_PAUSE,
    SUPPORT_RETURN_HOME,
    SUPPORT_SEND_COMMAND,
    SUPPORT_START,
    SUPPORT_STATE,
    SUPPORT_STATUS,
    SUPPORT_STOP,
    StateVacuumEntity,
)
from homeassistant.core import Config
from homeassistant.exceptions import ConditionErrorMessage
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DOMAIN, ERRORCODES, HUSQVARNA_URL, ICON

SUPPORT_STATE_SERVICES = (
    SUPPORT_STATE
    | SUPPORT_BATTERY
    | SUPPORT_MAP
    | SUPPORT_PAUSE
    | SUPPORT_RETURN_HOME
    | SUPPORT_SEND_COMMAND
    | SUPPORT_START
    | SUPPORT_STATE
    | SUPPORT_STATUS
    | SUPPORT_STOP
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Setup sensor platform."""

    session = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        HusqvarnaAutomowerEntity(session, idx)
        for idx, ent in enumerate(session.data["data"])
    )
    platform = entity_platform.current_platform.get()

    platform.async_register_entity_service(
        "park_and_start",
        {
            vol.Required("command"): cv.string,
            vol.Required("duration"): vol.Coerce(int),
        },
        "async_park_and_start",
    )

    platform.async_register_entity_service(
        "calendar",
        {
            vol.Required("start"): cv.time,
            vol.Required("end"): cv.time,
            vol.Required("monday"): cv.boolean,
            vol.Required("tuesday"): cv.boolean,
            vol.Required("wednesday"): cv.boolean,
            vol.Required("thursday"): cv.boolean,
            vol.Required("friday"): cv.boolean,
            vol.Required("saturday"): cv.boolean,
            vol.Required("sunday"): cv.boolean,
        },
        "async_custom_calendar_command",
    )

    platform.async_register_entity_service(
        "custom_command",
        {
            vol.Required("command_type"): cv.string,
            vol.Required("json_string"): cv.string,
        },
        "async_custom_command",
    )


class HusqvarnaAutomowerEntity(StateVacuumEntity):
    """Defining each mower Entity."""

    def __init__(self, session, idx) -> None:
        self.session = session
        self.idx = idx
        mower = self.session.data["data"][self.idx]
        mower_attributes = self.__get_mower_attributes()

        self.mower_id = mower["id"]
        self.mower_name = mower_attributes["system"]["name"]
        self.model = mower_attributes["system"]["model"]
        self._available = None

        self.session.register_cb(
            lambda _: self.async_write_ha_state(), schedule_immediately=True
        )

    def __get_mower_attributes(self):
        return self.session.data["data"][self.idx]["attributes"]

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.mower_id)},
            name=self.mower_name,
            manufacturer="Husqvarna",
            model=self.model,
            configuration_url=HUSQVARNA_URL,
            suggested_area="Garden",
        )

    @property
    def available(self) -> bool:
        """Return True if the device is available."""
        available = False
        try:
            available = (
                self.__get_mower_attributes()["metadata"]["connected"]
                and self.session.data["data"][self.idx]["id"] == self.mower_id
            )
        except (IndexError, KeyError):
            pass

        if self._available != available:
            if self._available is not None:
                if available:
                    _LOGGER.info("Connected to %s again", self.mower_name)
                else:
                    _LOGGER.warning("Connection to %s lost", self.mower_name)
            self._available = available

        return self._available

    @property
    def name(self) -> str:
        """Return the name of the mower."""
        return self.mower_name

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this mower."""
        return self.session.data["data"][self.idx]["id"]

    @property
    def state(self) -> str:
        """Return the state of the mower."""
        mower_attributes = self.__get_mower_attributes()
        if mower_attributes["mower"]["state"] in ["PAUSED"]:
            return STATE_PAUSED
        if mower_attributes["mower"]["state"] in [
            "WAIT_UPDATING",
            "WAIT_POWER_UP",
        ]:
            return STATE_IDLE
        if (mower_attributes["mower"]["state"] == "RESTRICTED") or (
            mower_attributes["mower"]["activity"] in ["PARKED_IN_CS", "CHARGING"]
        ):
            return STATE_DOCKED
        if mower_attributes["mower"]["activity"] in ["MOWING", "LEAVING"]:
            return STATE_CLEANING
        if mower_attributes["mower"]["activity"] == "GOING_HOME":
            return STATE_RETURNING
        if (
            mower_attributes["mower"]["state"]
            in [
                "FATAL_ERROR",
                "ERROR",
                "ERROR_AT_POWER_UP",
                "NOT_APPLICABLE",
                "UNKNOWN",
                "STOPPED",
                "OFF",
            ]
        ) or mower_attributes["mower"]["activity"] in [
            "STOPPED_IN_GARDEN",
            "UNKNOWN",
            "NOT_APPLICABLE",
        ]:
            return STATE_ERROR

    @property
    def error(self) -> str:
        """An error message if the vacuum is in STATE_ERROR."""
        if self.state == STATE_ERROR:
            mower_attributes = self.__get_mower_attributes()
            return ERRORCODES.get(mower_attributes["mower"]["errorCode"])
        return ""

    @property
    def icon(self) -> str:
        """Return the icon of the mower."""
        return ICON

    @property
    def supported_features(self) -> int:
        """Flag supported features."""
        return SUPPORT_STATE_SERVICES

    @property
    def battery_level(self) -> int:
        """Return the current battery level of the mower."""
        return max(
            0,
            min(
                100,
                self.__get_mower_attributes()["battery"]["batteryPercent"],
            ),
        )

    def __get_status(self) -> str:
        mower_attributes = self.__get_mower_attributes()
        next_start_short = ""
        if mower_attributes["planner"]["nextStartTimestamp"] != 0:
            next_start_dt_obj = datetime.fromtimestamp(
                (mower_attributes["planner"]["nextStartTimestamp"]) / 1000
            )
            next_start_short = next_start_dt_obj.strftime(", next start: %a %H:%M")
        if mower_attributes["mower"]["state"] == "UNKNOWN":
            return "Unknown"
        if mower_attributes["mower"]["state"] == "NOT_APPLICABLE":
            return "Not applicable"
        if mower_attributes["mower"]["state"] == "PAUSED":
            return "Paused"
        if mower_attributes["mower"]["state"] == "IN_OPERATION":
            if mower_attributes["mower"]["activity"] == "UNKNOWN":
                return "Unknown"
            if mower_attributes["mower"]["activity"] == "NOT_APPLICABLE":
                return "Not applicable"
            if mower_attributes["mower"]["activity"] == "MOWING":
                return "Mowing"
            if mower_attributes["mower"]["activity"] == "GOING_HOME":
                return "Going to charging station"
            if mower_attributes["mower"]["activity"] == "CHARGING":
                return f"Charging{next_start_short}"
            if mower_attributes["mower"]["activity"] == "LEAVING":
                return "Leaving charging station"
            if mower_attributes["mower"]["activity"] == "PARKED_IN_CS":
                return "Parked"
            if mower_attributes["mower"]["activity"] == "STOPPED_IN_GARDEN":
                return "Stopped"
        if mower_attributes["mower"]["state"] == "WAIT_UPDATING":
            return "Updating"
        if mower_attributes["mower"]["state"] == "WAIT_POWER_UP":
            return "Powering up"
        if mower_attributes["mower"]["state"] == "RESTRICTED":
            if mower_attributes["planner"]["restrictedReason"] == "WEEK_SCHEDULE":
                return f"Schedule{next_start_short}"
            if mower_attributes["planner"]["restrictedReason"] == "PARK_OVERRIDE":
                return "Park override"
            if mower_attributes["planner"]["restrictedReason"] == "SENSOR":
                return "Weather timer"
            if mower_attributes["planner"]["restrictedReason"] == "DAILY_LIMIT":
                return "Daily limit"
            if mower_attributes["planner"]["restrictedReason"] == "NOT_APPLICABLE":
                return "Parked until further notice"
        if mower_attributes["mower"]["state"] == "OFF":
            return "Off"
        if mower_attributes["mower"]["state"] == "STOPPED":
            return "Stopped"
        if mower_attributes["mower"]["state"] in [
            "ERROR",
            "FATAL_ERROR",
            "ERROR_AT_POWER_UP",
        ]:
            return ERRORCODES.get(mower_attributes["mower"]["errorCode"])
        return "Unknown"

    def __datetime_object(self, timestamp) -> datetime:
        """Converts the mower local timestamp to a UTC datetime object"""
        self.timestamp = timestamp
        self.naive = datetime.fromtimestamp(self.timestamp / 1000)
        return dt_util.as_local(self.naive)

    @property
    def extra_state_attributes(self) -> dict:
        """Return the specific state attributes of this mower."""
        mower_attributes = self.__get_mower_attributes()
        error_message = None
        error_time = None
        if mower_attributes["mower"]["state"] in [
            "ERROR",
            "FATAL_ERROR",
            "ERROR_AT_POWER_UP",
        ]:
            error_message = ERRORCODES.get(mower_attributes["mower"]["errorCode"])

            error_time = self.__datetime_object(
                mower_attributes["mower"]["errorCodeTimestamp"]
            )

        next_start = None

        if mower_attributes["planner"]["nextStartTimestamp"] != 0:
            next_start = self.__datetime_object(
                mower_attributes["planner"]["nextStartTimestamp"]
            )

        return {
            ATTR_STATUS: self.__get_status(),
            "mode": mower_attributes["mower"]["mode"],
            "activity": mower_attributes["mower"]["activity"],
            "state": mower_attributes["mower"]["state"],
            "errorMessage": error_message,
            "errorTime": error_time,
            "nextStart": next_start,
            "action": mower_attributes["planner"]["override"]["action"],
            "restrictedReason": mower_attributes["planner"]["restrictedReason"],
        }

    async def async_start(self) -> None:
        """Resume schedule."""
        command_type = "actions"
        payload = '{"data": {"type": "ResumeSchedule"}}'
        try:
            await self.session.action(self.mower_id, payload, command_type)
        except ClientResponseError as exception:
            _LOGGER.error("Command couldn't be sent to the command que")

    async def async_pause(self) -> None:
        """Pauses the mower."""
        command_type = "actions"
        payload = '{"data": {"type": "Pause"}}'
        try:
            await self.session.action(self.mower_id, payload, command_type)
        except ClientResponseError as exception:
            _LOGGER.error("Command couldn't be sent to the command que")

    async def async_stop(self, **kwargs) -> None:
        """Parks the mower until next schedule."""
        command_type = "actions"
        payload = '{"data": {"type": "ParkUntilNextSchedule"}}'
        try:
            await self.session.action(self.mower_id, payload, command_type)
        except ClientResponseError as exception:
            _LOGGER.error("Command couldn't be sent to the command que")

    async def async_return_to_base(self, **kwargs) -> None:
        """Parks the mower until further notice."""
        command_type = "actions"
        payload = '{"data": {"type": "ParkUntilFurtherNotice"}}'
        try:
            await self.session.action(self.mower_id, payload, command_type)
        except ClientResponseError as exception:
            _LOGGER.error("Command couldn't be sent to the command que")

    async def async_park_and_start(self, command, duration, **kwargs) -> None:
        """Sends a custom command to the mower."""
        command_type = "actions"
        string = {
            "data": {
                "type": command,
                "attributes": {"duration": duration},
            }
        }
        payload = json.dumps(string)
        try:
            await self.session.action(self.mower_id, payload, command_type)
        except ClientResponseError as exception:
            _LOGGER.error("Command couldn't be sent to the command que")

    async def async_custom_calendar_command(
        self,
        start,
        end,
        monday,
        tuesday,
        wednesday,
        thursday,
        friday,
        saturday,
        sunday,
        **kwargs,
    ) -> None:
        """Sends a custom calendar command to the mower."""
        start_in_minutes = start.hour * 60 + start.minute
        _LOGGER.debug("start in minutes int: %i", start_in_minutes)
        end_in_minutes = end.hour * 60 + end.minute
        _LOGGER.debug("end in minutes: %i", end_in_minutes)
        duration = end_in_minutes - start_in_minutes
        if duration <= 0:
            raise ConditionErrorMessage("<", "StartingTime must be before EndingTime")
        command_type = "calendar"
        string = {
            "data": {
                "type": "calendar",
                "attributes": {
                    "tasks": [
                        {
                            "start": start_in_minutes,
                            "duration": duration,
                            "monday": monday,
                            "tuesday": tuesday,
                            "wednesday": wednesday,
                            "thursday": thursday,
                            "friday": friday,
                            "saturday": saturday,
                            "sunday": sunday,
                        }
                    ]
                },
            }
        }
        payload = json.dumps(string)
        try:
            await self.session.action(self.mower_id, payload, command_type)
        except ClientResponseError as exception:
            _LOGGER.error("Command couldn't be sent to the command que")

    async def async_custom_command(self, command_type, json_string, **kwargs) -> None:
        """Sends a custom command to the mower."""
        try:
            await self.session.action(self.mower_id, json_string, command_type)
        except ClientResponseError as exception:
            _LOGGER.error("Command couldn't be sent to the command que")
