# LDAP authentication for Moonraker
#
# Copyright (C) 2022 Eric Callahan <arksine.code@gmail.com>
# Copyright (C) 2022 Luca Schöneberg <luca-schoeneberg@outlook.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations
import asyncio
import logging
import ldap3
from ldap3.core.exceptions import LDAPExceptionError
from ldap3.utils.conv import escape_filter_chars

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Optional
)

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from ldap3.abstract.entry import Entry

VALID_MEMBERSHIP_ATTRS = ["memberOf", "isMemberOf"]

class MoonrakerLDAP:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.ldap_host = config.get('ldap_host')
        self.ldap_port = config.getint("ldap_port", None)
        self.ldap_secure = config.getboolean("ldap_secure", False)
        self.membership_attr = config.getchoice(
            "membership_attribute", VALID_MEMBERSHIP_ATTRS, "memberOf"
        )
        self.check_dn_case = config.getboolean("check_dn_case", True)
        base_dn_template = config.gettemplate('base_dn')
        self.base_dn = base_dn_template.render()
        self.group_dn: Optional[str] = None
        group_dn_template = config.gettemplate("group_dn", None)
        if group_dn_template is not None:
            self.group_dn = group_dn_template.render()
        self.active_directory = config.getboolean('is_active_directory', False)
        self.bind_dn: Optional[str] = None
        self.bind_password: Optional[str] = None
        bind_dn_template = config.gettemplate('bind_dn', None)
        bind_pass_template = config.gettemplate('bind_password', None)
        if bind_dn_template is not None:
            self.bind_dn = bind_dn_template.render()
            if bind_pass_template is None:
                raise config.error(
                    "Section [ldap]: Option 'bind_password' is "
                    "required when 'bind_dn' is provided"
                )
            self.bind_password = bind_pass_template.render()
        self.user_filter: Optional[str] = None
        user_filter_template = config.gettemplate('user_filter', None)
        if user_filter_template is not None:
            self.user_filter = user_filter_template.render()
            if "USERNAME" not in self.user_filter:
                raise config.error(
                    "Section [ldap]: Option 'user_filter' is "
                    "is missing required token USERNAME"
                )
        self.lock = asyncio.Lock()

    async def authenticate_ldap_user(self, username: str, password: str) -> None:
        eventloop = self.server.get_event_loop()
        async with self.lock:
            await eventloop.run_in_thread(
                self._perform_ldap_auth, username, password
            )

    def _perform_ldap_auth(self, username: str, password: str) -> None:
        server = ldap3.Server(
            self.ldap_host, self.ldap_port, use_ssl=self.ldap_secure,
            connect_timeout=10.
        )
        conn_args = {
            "user": self.bind_dn,
            "password": self.bind_password,
            "auto_bind": ldap3.AUTO_BIND_NO_TLS,
        }
        attr_name = "sAMAccountName" if self.active_directory else "uid"
        escaped_user = escape_filter_chars(username)
        ldfilt = f"(&(objectClass=Person)({attr_name}={escaped_user}))"
        if self.user_filter:
            ldfilt = self.user_filter.replace("USERNAME", escaped_user)
        try:
            with ldap3.Connection(server, **conn_args) as conn:
                search_attrs = None if self.group_dn is None else [self.membership_attr]
                ret = conn.search(
                    self.base_dn, ldfilt, attributes=search_attrs
                )
                if not ret:
                    logging.info(f"LDAP User '{username}' Not Found")
                    raise self.server.error("Unauthorized", 401)
                user: Entry = conn.entries[0]
                rebind_success = conn.rebind(user.entry_dn, password)
            if not rebind_success:
                # Server may not allow rebinding, attempt to start
                # a new connection to validate credentials
                logging.debug(
                    "LDAP Rebind failed, attempting to validate credentials "
                    "with new connection."
                )
                conn_args["user"] = user.entry_dn
                conn_args["password"] = password
                with ldap3.Connection(server, **conn_args) as conn:
                    if self._validate_group(username, user):
                        return
            elif self._validate_group(username, user):
                return
        except LDAPExceptionError:
            logging.exception("LDAP authentication failed")
        else:
            logging.info("Invalid LDAP Username or Password")
        raise self.server.error("Unauthorized", 401)

    def _validate_group(self, username: str, user: Entry) -> bool:
        if self.group_dn is None:
            logging.debug(f"LDAP User {username} login successful")
            return True
        member_attr = getattr(user, self.membership_attr, None)
        if member_attr is None:
            logging.info(
                f"Membership attribute {self.membership_attr} not present "
                "in user entry"
            )
            return False
        groups: list[str] = getattr(member_attr, "values", [])
        req_dn = self.group_dn
        logging.debug(f"Required Group DN: {req_dn}, User Group DNs: {groups}")
        if not self.check_dn_case:
            groups = [grp.lower() for grp in groups]
            req_dn = req_dn.lower()
        if req_dn in groups:
            logging.debug(
                f"LDAP User {username} group match success, login successful"
            )
            return True
        logging.info(f"User '{username}' is not a member of configured group_dn.")
        return False


def load_component(config: ConfigHelper) -> MoonrakerLDAP:
    return MoonrakerLDAP(config)
