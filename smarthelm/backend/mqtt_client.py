"""
mqtt_client.py — MQTT event publisher for SmartHelm.

Publishes eye-state events to:
    smarthelm/{cam}/eye_state

Reconnects automatically using paho-mqtt's built-in loop_start() mechanism.
publish() is non-blocking and logs a warning on failure — never raises.

Standalone test (requires Mosquitto running on localhost:1883):
    python mqtt_client.py
"""

import sys
import os
import json
import time
import logging

import paho.mqtt.client as mqtt

sys.path.insert(0, os.path.dirname(__file__))
import config

logger = logging.getLogger(__name__)


class MQTTPublisher:
    """
    Thin paho-mqtt wrapper with auto-reconnect and non-blocking publish.

    Usage:
        pub = MQTTPublisher()
        pub.connect()
        pub.publish("CAM1", "CLOSED", 0.91, 34.0, True)
        pub.disconnect()
    """

    def __init__(
        self,
        broker: str = None,
        port: int = None,
        client_id: str = "smarthelm-backend",
    ):
        self._broker = broker or config.MQTT_BROKER
        self._port = port or config.MQTT_PORT
        self._connected = False

        # CallbackAPIVersion.VERSION1 required for paho-mqtt 2.x (RPi installs latest)
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION1,
            client_id=client_id,
            clean_session=True,
        )
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_publish = self._on_publish

        # paho auto-reconnect settings
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def connect(self):
        """
        Start the MQTT connection and background network loop.
        Non-blocking. Connected state is updated via on_connect callback.
        """
        try:
            self._client.connect(self._broker, self._port, keepalive=60)
            self._client.loop_start()
            logger.info(f"MQTT connecting to {self._broker}:{self._port}")
        except Exception as e:
            logger.error(f"MQTT connect failed: {e}. Will retry automatically.")

    def disconnect(self):
        """Graceful disconnect and stop network loop."""
        self._client.loop_stop()
        self._client.disconnect()
        logger.info("MQTT disconnected")

    def publish(
        self,
        cam: str,
        eye_state: str,
        confidence: float,
        perclos: float,
        alert: bool,
    ) -> bool:
        """
        Build and publish a JSON event payload.
        Returns True if message was queued, False if not connected.
        Never raises.
        """
        if not self._connected:
            logger.debug("MQTT not connected, skipping publish")
            return False

        payload = {
            "cam": cam,
            "ts": int(time.time()),
            "eye_state": eye_state,
            "confidence": round(float(confidence), 3),
            "perclos": round(float(perclos), 2),
            "alert": bool(alert),
        }
        topic = config.MQTT_TOPIC_TEMPLATE.format(cam=cam)

        try:
            result = self._client.publish(topic, json.dumps(payload), qos=1)
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                logger.warning(f"MQTT publish error rc={result.rc}")
                return False
            return True
        except Exception as e:
            logger.warning(f"MQTT publish exception: {e}")
            return False

    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self._connected = True
            logger.info(f"MQTT connected to {self._broker}:{self._port}")
        else:
            self._connected = False
            logger.error(f"MQTT connection refused, rc={rc}")

    def _on_disconnect(self, client, userdata, rc):
        self._connected = False
        if rc != 0:
            logger.warning(f"MQTT unexpected disconnect (rc={rc}), will auto-reconnect")

    def _on_publish(self, client, userdata, mid):
        logger.debug(f"MQTT message published mid={mid}")


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("MQTT publisher test — publish 5 events then quit")
    print("Run: mosquitto_sub -t 'smarthelm/#' -v  to see messages")

    pub = MQTTPublisher()
    pub.connect()
    time.sleep(1)  # give time to connect

    for i in range(5):
        state = "CLOSED" if i % 2 == 0 else "OPEN"
        ok = pub.publish("CAM1", state, 0.91, 14.0 + i * 3, i % 2 == 0)
        print(f"  Published #{i+1}: state={state}, ok={ok}")
        time.sleep(1)

    pub.disconnect()
    print("Done.")
