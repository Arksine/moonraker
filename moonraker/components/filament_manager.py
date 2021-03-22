# Filament Manager for printer
#
# Copyright (C) 2021 Mateusz Brzezinski <mateusz.brzezinski@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import logging
import time
import math
from typing import Set
from typing import Optional
from typing import List
from components.database import NamespaceWrapper
from websockets import WebRequest

FILAMENT_NAMESPACE = "filament_manager"
SPOOLS_SUB_NS = "spools"
ACTIVE_SPOOL_KEY = "active-spool"

MATERIALS = {
    'PLA': {'density': 1.24},
    'PLA_plus': {'density': 1.24},
    'ABS': {'density': 1.04},
    'PETG': {'density': 1.27},
    'NYLON': {'density': 1.52},
    'TPU': {'density': 1.21},
    'PC': {'density': 1.3},
    'Carbon': {'density': 1.3},
    'PC_ABS': {'density': 1.19},
    'HIPS': {'density': 1.03},
    'PVA': {'density': 1.23},
    'ASA': {'density': 1.05},
    'PP': {'density': 0.9},
    'POM': {'density': 1.4},
    'PMMA': {'density': 1.18},
    'FPE': {'density': 2.16}
}


class Validation:
    def validate(self) -> Set[str]:
        failed = filter(lambda f: self.__getattribute__(f) is None,
                        self._required_attributes)
        return set(failed)


class Spool(Validation):
    _required_attributes: Set[str] = {'name'}

    def __init__(self, data={}):
        self.name: str = None
        self.active: bool = True
        self.color_name: str = None
        self.color_code: str = None
        self.vendor: str = None
        self.material: str = None
        self.density: float = None
        self.diameter: float = None
        self.total_weight: int = None
        self.used_weight: int = 0
        self.total_length: int = None
        self.used_length: int = 0
        self.first_used: float = None
        self.last_used: float = None
        self.cost: float = None
        self.comment: str = None

        self.update(data)

    def update(self, data):
        for a in data:
            if hasattr(self, a):
                setattr(self, a, data[a])

    def serialize(self):
        return self.__dict__.copy()


class FilamentManager:
    def __init__(self, config):
        self.server = config.get_server()

        database = self.server.lookup_component("database")
        database.register_local_namespace(FILAMENT_NAMESPACE)
        self.db: NamespaceWrapper = database.wrap_namespace(FILAMENT_NAMESPACE,
                                                            parse_keys=True)

        self.endpoint = FilamentManagerEndpoint(self.server, self)
        self.listener = FilamentManagerListener(self.server, self)

    def _spool_id(self, spool_id: str) -> str:
        return f"{SPOOLS_SUB_NS}.{spool_id}"

    def find_spool(self, spool_id: str, default = None) -> Optional[dict]:
        return self.db.get(self._spool_id(spool_id), default)

    def set_active_spool(self, spool_id: str) -> Optional[dict]:
        spool = self.find_spool(spool_id)

        if spool:
            if self.db.get(ACTIVE_SPOOL_KEY):
                self.db.update_child(ACTIVE_SPOOL_KEY, spool_id)
            else:
                self.db.insert(ACTIVE_SPOOL_KEY, spool_id)

            logging.info(f'Setting spool active, id: {spool_id}')
            return spool
        else:
            return None

    def get_active_spool_id(self) -> str:
        return self.db.get(ACTIVE_SPOOL_KEY, None)

    def add_spool(self, data: {}) -> str:
        spool = Spool(data)
        missing_attrs = spool.validate()
        if missing_attrs:
            raise self.server.error(
                f"Missing spool attributes: {missing_attrs}", 404)

        next_spool_id = 0
        spools = self.db.get(SPOOLS_SUB_NS, [])
        if spools:
            next_spool_id = int(list(spools)[-1], 16) + 1
        spool_id = f"{next_spool_id:06X}"

        self.db.insert(self._spool_id(spool_id), spool.serialize())
        logging.info(f'New spool added, id: {spool_id}')

        return spool_id

    def update_spool(self, spool_id: str, data: {}) -> None:
        spool_from_db = self.find_spool(spool_id)

        spool = Spool(spool_from_db)
        spool.update(data)
        missing_attrs = spool.validate()
        if missing_attrs:
            raise self.server.error(
                f"Missing spool attributes: {missing_attrs}", 404)

        self.db.update_child(self._spool_id(spool_id), spool.serialize())
        logging.info(f'Spool id: {spool_id} updated.')

        return

    def delete_spool(self, spool_id: str) -> None:
        self.db.delete(self._spool_id(spool_id))
        logging.info(f'Spool id: {spool_id} deleted.')
        return

    def find_all_spools(self, show_inactive: bool) -> List[dict]:
        spools = self.db.get(SPOOLS_SUB_NS, [])

        if not show_inactive:
            spools = {k: v for k, v in spools.items() if v['active'] == True}

        return spools

    def track_filament_usage(self, job_id: str, used_length: float):
        spool_id = self.get_active_spool_id()
        spool = self.find_spool(spool_id)

        if spool:
            old_used_length = spool['used_length']
            new_used_length = old_used_length + used_length
            spool['used_length'] = new_used_length

            diameter = spool['diameter']
            density = spool['density']
            old_used_weight = spool['used_weight']
            cost = spool['cost']
            total_weight = spool['total_weight']
            used_weight = 0
            new_used_weight = 0
            used_cost = 0

            if diameter and density:
                r = diameter/2
                density_mm = density/1000
                used_weight = math.pi * r * r * used_length * density_mm
                new_used_weight = old_used_weight + used_weight
                spool['used_weight'] = new_used_weight

            if 0 < total_weight < used_weight:
                spool['active'] = False

            if cost and used_weight and total_weight:
                used_cost = used_weight/total_weight*cost

            if not spool['first_used']:
                spool['first_used'] = time.time()

            spool['last_used'] = time.time()

            self.update_spool(spool_id, spool)

            history = self.server.lookup_component('history', None)
            metadata = {'spool_id': spool_id,
                        'used_weight': used_weight,
                        'cost': used_cost}
            history.add_job_metadata(job_id, {'filament': metadata})

            logging.info(f'Tracking filament usage, spool_id: {spool_id}, ' +
                         f'length: {used_length}, ' +
                         f'old used_length: {old_used_length}, ' +
                         f'new used_length: {new_used_length} ' +
                         f'weight: {used_weight}, ' +
                         f'old used_weight: {old_used_weight}, ' +
                         f'new used_weight: {new_used_weight}, ' +
                         f'cost: {used_cost}')
        else:
            logging.info("Active spool is not set, tracking ignored")


class FilamentManagerListener:
    def __init__(self, server, filament_manager: FilamentManager):
        self.filament_manager = filament_manager
        self.server = server

        self._register_listeners()

    def _register_listeners(self):
        self.server.register_event_handler('history:history_changed',
                                           self._handle_history_changed)

    def _handle_history_changed(self, data: {}):
        action = data['action']

        if action == 'finished':
            job_data = data['job']
            job_id = job_data['job_id']
            filament_used = job_data['filament_used']
            self.filament_manager.track_filament_usage(job_id, filament_used)


class FilamentManagerEndpoint:
    def __init__(self, server, filament_manager: FilamentManager):
        self.filament_manager = filament_manager
        self.server = server

        self._register_endpoints()

    def _register_endpoints(self):
        self.server.register_endpoint(
            "/filament/spool", ['GET', 'POST', 'DELETE'],
            self._handle_spool_request)
        self.server.register_endpoint(
            "/filament/spool/list", ['GET'], self._handle_spools_list)
        self.server.register_endpoint(
            "/filament/active_spool", ['GET', 'POST'],
            self._handle_active_spool)
        self.server.register_endpoint(
            "/filament/materials", ['GET'],
            self._handle_materials_list)

    async def _handle_spool_request(self, web_request: WebRequest):
        action = web_request.get_action()

        if action == 'GET':
            spool_id = web_request.get_str('id')
            spool = self.filament_manager.find_spool(spool_id)
            return {'spool': spool}
        elif action == 'POST':
            spool_id = web_request.get('id', None)

            if spool_id:
                self.filament_manager.update_spool(spool_id, web_request.args)
                return 'OK'
            else:
                spool_id = self.filament_manager.add_spool(web_request.args)
                return {'spool_added': spool_id}
        elif action == 'DELETE':
            spool_id = web_request.get_str('id')
            self.filament_manager.delete_spool(spool_id)
            return 'OK'

    async def _handle_spools_list(self, web_request: WebRequest):
        show_inactive = web_request.get_boolean('show_inactive', False)
        spools = self.filament_manager.find_all_spools(show_inactive)

        return {'spools': spools}

    async def _handle_active_spool(self, web_request: WebRequest):
        action = web_request.get_action()

        if action == 'GET':
            spool_id = self.filament_manager.get_active_spool_id()
            return {"spool_id": spool_id}
        elif action == 'POST':
            spool_id = web_request.get_str('id')
            spool = self.filament_manager.set_active_spool(spool_id)
            if spool:
                return 'OK'
            else:
                raise self.server.error(
                    f"Spool id {spool_id} not found", 404)

    async def _handle_materials_list(self, web_request: WebRequest):
        return {'materials': MATERIALS}


def load_component(config):
    return FilamentManager(config)
