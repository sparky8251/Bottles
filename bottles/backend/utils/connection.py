# connection.py
#
# Copyright 2025 mirkobrombin <brombin94@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, in version 3 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

import os
from datetime import datetime
from gettext import gettext as _
from typing import Optional


from bottles.backend.logger import Logger
from bottles.backend.models.result import Result
from bottles.backend.utils.httpclient import PING_TIMEOUT, Cancelled, head_ok
from bottles.backend.state import Notification, SignalManager, Signals

logging = Logger()


class ConnectionUtils:
    """
    This class is used to check the connection, pinging the official
    Bottle's website. If the connection is offline, the user will be
    notified and False will be returned, otherwise True.
    """

    _status: Optional[bool] = None
    last_check = None

    _check_urls = (
        "https://proxy.usebottles.com/repo/components/",
        "https://github.com",
        "https://cloudflare.com",
    )

    def __init__(self, force_offline=False, **kwargs):
        super().__init__(**kwargs)
        self.force_offline = force_offline
        self.do_check_connection = True
        self.aborted_connections = 0
        SignalManager.connect(Signals.ForceStopNetworking, self.stop_check)

    @property
    def status(self) -> Optional[bool]:
        return self._status

    @status.setter
    def status(self, value: bool):
        if value is None:
            logging.error("Cannot set network status to None")
            return
        self._status = value
        SignalManager.send(Signals.NetworkStatusChanged, Result(status=self.status))

    def stop_check(self, res: Result):
        if res.status:
            self.do_check_connection = False

    def __ping(self, url: str) -> bool:
        # bounded by PING_TIMEOUT so a stalled or filtered connection cannot
        # hang startup; a timeout simply means offline
        try:
            return head_ok(url, timeout=PING_TIMEOUT, cancel=lambda: not self.do_check_connection)
        except Cancelled:
            self.aborted_connections += 1
            raise

    def check_connection(self, show_notification=False) -> Optional[bool]:
        """check network status, send result through signal NetworkReady and return"""
        if self.force_offline or "FORCE_OFFLINE" in os.environ:
            logging.info("Forcing offline mode")
            self.status = False
            return False

        try:
            online = False
            for url in self._check_urls:
                if not self.do_check_connection:
                    break
                try:
                    if self.__ping(url):
                        online = True
                        break
                except Exception:
                    continue

            if not online:
                raise Exception("Connection status: offline …")

            self.last_check = datetime.now()
            self.status = True
        except Exception:
            logging.warning("Connection status: offline …")
            if show_notification:
                SignalManager.send(
                    Signals.GNotification,
                    Result(
                        True,
                        Notification(
                            title="Bottles",
                            text=_("You are offline, unable to download."),
                            image="network-wireless-disabled-symbolic",
                        ),
                    ),
                )
            self.last_check = datetime.now()
            self.status = False
        
        self.do_check_connection = True
        return self.status
