from __future__ import annotations
from re import L
import pytest
import pytest_asyncio
import asyncio
import copy
from inspect import isawaitable
from moonraker.server import Server
from moonraker.utils import ServerError
from typing import TYPE_CHECKING, AsyncIterator, Dict, Any, Iterator

if TYPE_CHECKING:
    from components.database import MoonrakerDatabase
    from components.database import NamespaceWrapper
    from fixtures import HttpClient, WebsocketClient

TEST_DB: Dict[str, Dict[str, Any]] = {
    "automobiles": {
        "chevy": {
            "camaro": "silver",
            "silverado": {
                "1500": 3,
                "2500": 1
            }
        },
        "ford": {
            "mustang": "red",
            "f-series": {
                "f150": [150, "black"],
                "f350": {
                    "platinum": 10000,
                }
            }
        }
    },
    "fruits": {
        "apples": {
            "granny_smith": 10,
            "red_delicious": 8
        },
        "oranges": 50,
        "bananas": True
    },
    "vegetables": {
        "tomato": "nope"
    },
    "books": {
        "fantasy": {
            "lotr": "Gandalf"
        },
        "science_fiction": "dune"
    },
    "planets": {
        "earth": {
            "biosphere": True,
            "color": "blue"
        },
        "venus": {
            "hot": True
        },
        "mars": {
            "color": "red"
        },
        "jupiter": {
            "gas_giant": True,
            "europa": {
                "diameter": 3121.6
            },
            "io": "closest"
        },
        "saturn": {
            "has_rings": True
        },
        "pluto": "Don't unplanet me!"
    }
}

TEST_RECORD = {
    "debian": {
        "ubuntu": 10,
        "mint": True
    },
    "arch": 100,
    "redhat": {
        "centos": False
    }
}

TEST_OVERWRITE = {
    "vegetables": {
        "celery": "ranch",
        "lettuce": 100,
        "spinich": "popeye"
    },
    "oses": TEST_RECORD
}

@pytest_asyncio.fixture(scope="class")
async def base_db(base_server: Server) -> AsyncIterator[MoonrakerDatabase]:
    db: MoonrakerDatabase = base_server.load_component(
        base_server.config, "database")
    for ns, record in TEST_DB.items():
        for record_name, value in record.items():
            db.insert_item(ns, record_name, value)
    yield db
    await db.close()

@pytest_asyncio.fixture(scope="class")
async def running_db(base_server: Server) -> AsyncIterator[MoonrakerDatabase]:
    base_server.load_components()
    db: MoonrakerDatabase = base_server.lookup_component("database")
    for ns, record in TEST_DB.items():
        for record_name, value in record.items():
            db.insert_item(ns, record_name, value)
    await base_server.server_init(False)
    await base_server.start_server(False)
    yield db
    await base_server._stop_server("terminate")

# check_future() only resolves futures that are complete.  This
# is done to test database behavior in __init__() methods, where
# it is not possible to await a result.  We can't make this method
# async, as we need to check the future immediately.  Using an
# async would cause it to be scheduled on the event loop, with
# a thread potentially resolving a future before we can check it.
def check_future(fut: asyncio.Future,
                 db: MoonrakerDatabase
                 ) -> Any:
    server = db.server
    if server.is_running():
        if fut.done():
            pytest.fail("Future done while server running")
        return fut
    elif not fut.done():
        pytest.fail("Future not ready before server start")
    return fut.result()

@pytest.mark.asyncio
class BaseTest:
    @pytest.fixture(scope="class")
    def db(self, base_db):
        return base_db

@pytest.mark.asyncio
class ThreadedTest:
    @pytest.fixture(scope="class")
    def db(self, running_db):
        return running_db

class TestInstantiation:
    @pytest.fixture(scope="class")
    def db(self,
           base_server: Server,
           event_loop: asyncio.AbstractEventLoop
           ) -> Iterator[MoonrakerDatabase]:
        db: MoonrakerDatabase
        db = base_server.load_component(base_server.config, "database")
        yield db
        event_loop.run_until_complete(db.close())

    def test_initial_state(self, db: MoonrakerDatabase):
        mrdb = db.get_item("moonraker").result()
        assert (
            list(db.namespaces.keys()) == ["moonraker"] and
            mrdb == {
                "database_version": 1,
                "database": {
                    "unsafe_shutdowns": 1
                }
            }
        )

    def test_wrap_invalid_namespace(self, db: MoonrakerDatabase):
        expected = "Namespace 'invalid' not found"
        with pytest.raises(ServerError, match=expected):
            db.wrap_namespace("invalid")

    def test_insert_record_nonetype(self, db: MoonrakerDatabase):
        ret = db._insert_record("moonraker", "test_key", None)
        assert ret is False

    def test_encode_error(self, db: MoonrakerDatabase):
        with pytest.raises(ServerError, match="Error encoding val"):
            db._encode_value(set(["invalid_value"]))

    def test_decode_error(self, db: MoonrakerDatabase):
        with pytest.raises(ServerError, match="Error decoding value"):
            db._decode_value(b"invalid")

class TestCoreServerLoaded:
    @pytest.fixture(scope="class")
    def db(self,
           base_server: Server,
           event_loop: asyncio.AbstractEventLoop
           ) -> Iterator[MoonrakerDatabase]:
        base_server.load_components()
        db: MoonrakerDatabase
        db = base_server.lookup_component("database")
        yield db
        event_loop.run_until_complete(
            base_server._stop_server("terminate"))

    def test_core_state(self, db: MoonrakerDatabase):
        mrdb = db.get_item("moonraker").result()
        expected_ns = ["gcode_metadata", "moonraker"]
        assert (
            sorted(db.namespaces.keys()) == expected_ns and
            mrdb == {
                "database_version": 1,
                "database": {
                    "protected_namespaces": expected_ns,
                    "unsafe_shutdowns": 1
                },
                "file_manager": {
                    "metadata_version": 3
                }
            }
        )

@pytest.mark.run_paths(database="bare_db.cdb")
class TestCoreServerPreloaded(TestCoreServerLoaded):
    def test_core_state(self, db: MoonrakerDatabase):
        expected_ns = [
            "moonraker", "gcode_metadata", "update_manager",
            "authorized_users", "history"
        ]
        mrdb = db.get_item("moonraker").result()
        assert (
            sorted(db.namespaces.keys()) == sorted(expected_ns) and
            mrdb["database"]["unsafe_shutdowns"] == 2
        )

class TestUnallowedMethods:
    def test_register_error(self, running_db: MoonrakerDatabase):
        with pytest.raises(ServerError):
            running_db.register_local_namespace("fruits")

    def test_wrap_namespace(self, running_db: MoonrakerDatabase):
        with pytest.raises(ServerError):
            running_db.wrap_namespace("fruits")

class TestInsertItem(BaseTest):
    async def test_insert_record(self, db: MoonrakerDatabase):
        db.insert_item("oses", "linux", TEST_RECORD)
        fut = db.get_item("oses", "linux")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result == TEST_RECORD

    async def test_insert_nested(self, db: MoonrakerDatabase):
        db.insert_item(
            "oses", "windows.eleven.feburary.2022", "ok")
        fut = db.get_item("oses", "windows.eleven.feburary.2022")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result == "ok"

    async def test_insert_nested_invalid_assign(self, db: MoonrakerDatabase):
        with pytest.raises(ServerError):
            ret = db.insert_item("oses", "linux.arch.february", "2022")
            await ret

    async def test_insert_nested_reduce_failure(self, db: MoonrakerDatabase):
        with pytest.raises(ServerError):
            ret = db.insert_item("oses", "linux.arch.february.2022", True)
            await ret

    async def test_overwrite_record(self, db: MoonrakerDatabase,
                                    caplog: pytest.LogCaptureFixture):
        db.insert_item("oses", "ios", 10)
        db.insert_item("oses", ["ios", "15.3"], True)
        fut = db.get_item("oses", "ios")
        expected_log = (
            "Warning: Key ios contains a value of type"
            " <class 'int'>. Overwriting with an object."
        )
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert (
            result == {"15.3": True} and
            expected_log in caplog.messages
        )

class TestInsertItemThreaded(ThreadedTest, TestInsertItem):
    pass
class TestGetItem(BaseTest):
    async def test_get_record(self, db: MoonrakerDatabase):
        fut = db.get_item("automobiles", "chevy")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result == TEST_DB["automobiles"]["chevy"]

    async def test_get_namespace(self, db: MoonrakerDatabase):
        fut = db.get_item("fruits")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result == TEST_DB["fruits"]

    async def test_get_namespace_fail(self, db: MoonrakerDatabase):
        with pytest.raises(ServerError):
            ret = db.get_item("trains")
            await ret

    async def test_get_namespace_default(self, db: MoonrakerDatabase):
        fut = db.get_item("trains", default={})
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result == {}

    async def test_get_record_fail(self, db: MoonrakerDatabase):
        with pytest.raises(ServerError):
            ret = db.get_item("automobiles", "toyota")
            await ret

    async def test_get_record_default(self, db: MoonrakerDatabase):
        fut = db.get_item("automobiles", "toyota", {})
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result == {}

    async def test_get_key_fail(self, db: MoonrakerDatabase):
        with pytest.raises(ServerError):
            ret = db.get_item("automobiles", "chevy.equinox")
            await ret

    async def test_get_key_fail_default(self, db: MoonrakerDatabase):
        fut = db.get_item("automobiles", "chevy.equinox", "suv")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result == "suv"

    async def test_get_nested(self, db: MoonrakerDatabase):
        fut = db.get_item(
            "automobiles", "ford.f-series.f350.platinum")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result == 10000

    async def test_get_nested_no_key(self, db: MoonrakerDatabase):
        with pytest.raises(ServerError):
            ret = db.get_item("automobiles", "ford.f-series.f350.superduty")
            await ret

    async def test_get_nested_no_key_default(self, db: MoonrakerDatabase):
        fut = db.get_item(
            "automobiles", "ford.f-series.f350.superduty", "success")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result == "success"

    async def test_get_record_invalid_key(self, db: MoonrakerDatabase):
        with pytest.raises(ServerError):
            ret = db.get_item("fruits", "apples..red_delicious")
            await ret

    async def test_get_record_invalid_key_type(self, db: MoonrakerDatabase):
        with pytest.raises(ServerError):
            ret = db.get_item("vegetables", 100)
            await ret

    async def test_get_record_key_list(self, db: MoonrakerDatabase):
        key = ["chevy", "silverado", "2500"]
        fut = db.get_item("automobiles", key)
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result == 1

class TestGetItemThreaded(ThreadedTest, TestGetItem):
    pass

class TestUpdateItem(BaseTest):
    async def test_update_record(self, db: MoonrakerDatabase):
        update_val = {
            "granny_smith": 1000,
            "jazz": 10.8,
            "gala": {"bland": True}
        }
        db.update_item("fruits", "apples", update_val)
        fut = db.get_item("fruits", "apples")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result == {
            "granny_smith": 1000,
            "red_delicious": 8,
            "jazz": 10.8,
            "gala": {"bland": True}
        }

    async def test_update_nested(self, db: MoonrakerDatabase):
        update_val = {"3500": {"color": "green"}, "2500": None}
        db.update_item("automobiles", "chevy.silverado", update_val)
        fut = db.get_item("automobiles", "chevy")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result == {
            "camaro": "silver",
            "silverado": {
                "1500": 3,
                "2500": None,
                "3500": {"color": "green"}
            }
        }

    async def test_update_replace_nested(self, db: MoonrakerDatabase):
        db.update_item("fruits", "apples.gala.bland", "ok")
        fut = db.get_item("fruits", "apples")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result == {
            "granny_smith": 1000,
            "red_delicious": 8,
            "jazz": 10.8,
            "gala": {"bland": "ok"}
        }

    async def test_update_replace_nested_dict(self, db: MoonrakerDatabase):
        db.update_item("automobiles", "ford.f-series.f350", "tow")
        fut = db.get_item("automobiles", "ford")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result == {
            "mustang": "red",
            "f-series": {
                "f150": [150, "black"],
                "f350": "tow"
            }
        }

    async def test_update_namespace_fail(self, db: MoonrakerDatabase):
        with pytest.raises(ServerError):
            ret = db.update_item("pizza", "deepdish", {})
            await ret

    async def test_update_record_fail(self, db: MoonrakerDatabase):
        with pytest.raises(ServerError):
            ret = db.update_item("automobiles", "toyota", {})
            await ret

    async def test_update_replace_record_dict_fail(self, db: MoonrakerDatabase):
        with pytest.raises(ServerError):
            ret = db.update_item("fruits", "apples", None)
            await ret

    async def test_update_replace_record_dict(self, db: MoonrakerDatabase):
        db.update_item("fruits", "apples", ["success"])
        fut = db.get_item("fruits", "apples")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result == ["success"]

    async def test_update_key_fail(self, db: MoonrakerDatabase):
        with pytest.raises(ServerError):
            ret = db.update_item("automobiles", "ford.raptor", 10)
            await ret

    async def test_update_nested_key_fail(self, db: MoonrakerDatabase):
        with pytest.raises(ServerError):
            ret = db.update_item("automobiles", "ford.mustang.cobra", 10)
            await ret

    async def test_update_nested_key_not_found(self, db: MoonrakerDatabase):
        with pytest.raises(ServerError):
            ret = db.update_item("automobiles", "chevy.corvette.z06", 10)
            await ret

class TestUpdateItemThreaded(ThreadedTest, TestUpdateItem):
    pass


class TestDeleteItem(BaseTest):
    async def test_delete_nested_item(self, db: MoonrakerDatabase):
        del_fut = db.delete_item(
            "automobiles", "ford.f-series.f350.platinum")
        del_result = check_future(del_fut, db)
        if isawaitable(del_result):
            del_result = await del_result
        fut = db.get_item("automobiles", "ford")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert (
            del_result == 10000 and
            result == {
                "mustang": "red",
                "f-series": {
                    "f150": [150, "black"],
                    "f350": {}
                }
            }
        )

    async def test_delete_nested_dict(self, db: MoonrakerDatabase):
        del_fut = db.delete_item(
            "automobiles", "ford.f-series.f350")
        del_result = check_future(del_fut, db)
        if isawaitable(del_result):
            del_result = await del_result
        fut = db.get_item("automobiles", "ford")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert (
            del_result == {} and
            result == {
                "mustang": "red",
                "f-series": {
                    "f150": [150, "black"],
                }
            }
        )

    async def test_delete_fail(self, db: MoonrakerDatabase):
        with pytest.raises(ServerError):
            ret = db.delete_item("fruits", "bananas.green")
            await ret

    async def test_delete_record(self, db: MoonrakerDatabase):
        del_fut = db.delete_item("fruits", "bananas")
        del_result = check_future(del_fut, db)
        if isawaitable(del_result):
            del_result = await del_result
        fut = db.get_item("fruits", "bananas", None)
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert (
            del_result is True and
            result is None
        )

    async def test_delete_last_nested(self, db: MoonrakerDatabase):
        del_fut = db.delete_item("books", "fantasy.lotr")
        del_result = check_future(del_fut, db)
        if isawaitable(del_result):
            del_result = await del_result
        fut = db.get_item("books", "fantasy", None)
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert del_result == "Gandalf" and result is None

    async def test_drop_db(self, db: MoonrakerDatabase):
        del_fut = db.delete_item("vegetables", "tomato",
                                 drop_empty_db=True)
        del_result = check_future(del_fut, db)
        if isawaitable(del_result):
            del_result = await del_result
        fut = db.get_item("vegetables", default=None)
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert del_result == "nope" and result is None

class TestDeleteItemThreaded(ThreadedTest, TestDeleteItem):
    pass

class TestInsertBatch(BaseTest):
    async def test_insert_batch(self, db: MoonrakerDatabase):
        db.insert_batch("batch_test", TEST_DB)
        fut = db.get_item("batch_test")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result == TEST_DB

    async def test_insert_batch_overwrite(self, db: MoonrakerDatabase):
        expected = copy.deepcopy(TEST_DB)
        expected.update(TEST_OVERWRITE)
        db.insert_batch("batch_test", TEST_OVERWRITE)
        fut = db.get_item("batch_test")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result == expected

class TestInsertBatchThreaded(ThreadedTest, TestInsertBatch):
    pass

class TestGetBatch(BaseTest):
    async def test_get_batch(self, db: MoonrakerDatabase):
        keys = ["apples", "oranges", "bananas"]
        fut = db.get_batch("fruits", keys)
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result == TEST_DB["fruits"]

    async def test_get_batch_invalid_namespace(self, db: MoonrakerDatabase):
        with pytest.raises(ServerError):
            fut = db.get_batch("invalid", ["no", "key"])
            await fut

    async def test_get_batch_invalid_keys(self, db: MoonrakerDatabase):
        fut = db.get_batch("automobiles", ["chevy", "toyota", "dodge"])
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result == {"chevy": TEST_DB["automobiles"]["chevy"]}

    async def test_get_batch_no_valid_keys(self, db: MoonrakerDatabase):
        fut = db.get_batch("automobiles", ["toyota", "dodge"])
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result == {}

class TestGetBatchThreaded(ThreadedTest, TestGetBatch):
    pass

class TestMoveBatch(BaseTest):
    async def test_move_batch(self, db: MoonrakerDatabase):
        source_keys = list(TEST_DB["fruits"].keys())
        dest_keys = [f"super_{key}" for key in source_keys]
        expected = {dk: TEST_DB["fruits"][sk] for dk, sk in
                    zip(dest_keys, source_keys)}
        db.move_batch("fruits", source_keys, dest_keys)
        fut = db.get_item("fruits")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result == expected

    async def test_move_batch_invalid_namespace(self, db: MoonrakerDatabase):
        with pytest.raises(ServerError):
            fut = db.move_batch(
                "invalid_ns", ["super_banana", "super_apple"],
                ["banana", "apple"])
            await fut

    async def test_move_batch_invalid_key(self, db: MoonrakerDatabase):
        source_keys = ["chevy", "toyota", "dodge"]
        dest_keys = ["chevrolet", "lexus", "chrysler"]
        expected = copy.deepcopy(TEST_DB["automobiles"])
        expected["chevrolet"] = expected.pop("chevy")
        db.move_batch("automobiles", source_keys, dest_keys)
        fut = db.get_item("automobiles")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result == expected

    async def test_move_batch_no_valid_keys(self, db: MoonrakerDatabase):
        db.move_batch("vegetables", ["celery", "peas"],
                      ["no_celery", "no_peas"])
        fut = db.get_item("vegetables")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result == TEST_DB["vegetables"]

    async def test_move_batch_mismatch_key_length(self, db: MoonrakerDatabase):
        with pytest.raises(ServerError):
            ret = db.move_batch("books", ["science_fiction"],
                                ["science_fiction", "fantasy"])
            await ret

class TestMoveBatchThreaded(ThreadedTest, TestMoveBatch):
    pass

class TestDeleteBatch(BaseTest):
    async def test_delete_batch(self, db: MoonrakerDatabase):
        del_keys = ["mars", "venus", "pluto"]
        expected = copy.deepcopy(TEST_DB["planets"])
        for k in del_keys:
            del expected[k]
        db.delete_batch("planets", del_keys)
        fut = db.get_item("planets")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result == expected

    async def test_delete_batch_all_keys(self, db: MoonrakerDatabase):
        del_keys = (TEST_DB["automobiles"].keys())
        db.delete_batch("automobiles", del_keys)
        fut = db.get_item("automobiles")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result == {}

    async def test_delete_batch_invalid_namespace(self, db: MoonrakerDatabase):
        with pytest.raises(ServerError):
            ret = db.delete_batch("invalid", ["no", "key"])
            await ret

    async def test_delete_batch_invalid_key(self, db: MoonrakerDatabase):
        del_keys = ["science_fiction", "horror", "documentary"]
        db.delete_batch("books", del_keys)
        fut = db.get_item("books")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result == {"fantasy": {"lotr": "Gandalf"}}

    async def test_delete_batch_no_valid_keys(self, db: MoonrakerDatabase):
        del_keys = ["grapes", "peaches", "strawberries"]
        db.delete_batch("fruits", del_keys)
        fut = db.get_item("fruits")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result == TEST_DB["fruits"]

class TestDeleteBatchThreaded(ThreadedTest, TestDeleteBatch):
    pass

class TestUpdateNamespace(BaseTest):
    async def test_update_namespace(self, db: MoonrakerDatabase):
        update_val = {
            "venus": {"hot": True},
            "pluto": {"dwarf": True},
            "uranus": "klignons",
            "mercury": [1, 2, 3]
        }
        db.update_namespace("planets", update_val)
        fut = db.get_item("planets")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        expected = copy.deepcopy(TEST_DB["planets"])
        expected.update(update_val)
        assert result == expected

    async def test_update_namespace_invalid(self, db: MoonrakerDatabase):
        with pytest.raises(ServerError):
            ret = db.update_namespace("invalid", {"hello": False})
            await ret

class TestUpdateNamespaceThreaded(ThreadedTest, TestUpdateNamespace):
    pass

class TestClearNamespace(BaseTest):
    async def test_clear_namespace(self, db: MoonrakerDatabase):
        db.clear_namespace("fruits")
        fut = db.get_item("fruits")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result == {}

    async def test_clear_namespace_drop(self, db: MoonrakerDatabase):
        fut = db.clear_namespace("books", drop_empty_db=True)
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert "books" not in db.namespaces


    async def test_clear_namespace_invalid(self, db: MoonrakerDatabase):
        with pytest.raises(ServerError):
            fut = db.clear_namespace("invalid")
            await fut

class TestClearNamespaceThreaded(ThreadedTest, TestClearNamespace):
    pass

class TestSyncNamespace(BaseTest):
    async def test_sync_namespace(self, db: MoonrakerDatabase):
        synced = copy.deepcopy(TEST_DB["planets"])
        del synced["mars"]
        del synced["pluto"]
        synced.update({"mercury": "close", "neptune": "far",
                      "venus": "cloudy"})
        db.sync_namespace("planets", synced)
        fut = db.get_item("planets")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result == synced

    async def test_sync_no_overlap(self, db: MoonrakerDatabase):
        synced = {"toyota": {"corolla": "car", "tundra": "truck"}}
        db.sync_namespace("automobiles", synced)
        fut = db.get_item("automobiles")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result == synced

    async def test_sync_no_remove(self, db: MoonrakerDatabase):
        synced = copy.deepcopy(TEST_DB["fruits"])
        synced.update({"cherries": "sweet", "berries": {"blue": "mild"}})
        db.sync_namespace("fruits", synced)
        fut = db.get_item("fruits")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result == synced

    async def test_sync_namespace_empty(self, db: MoonrakerDatabase):
        with pytest.raises(ServerError):
            fut = db.sync_namespace("books", {})
            await fut

    async def test_sync_namespace_invalid(self, db: MoonrakerDatabase):
        with pytest.raises(ServerError):
            fut = db.sync_namespace("invalid", {"no": "key"})
            await fut

class TestSyncNamespaceThreaded(ThreadedTest, TestSyncNamespace):
    pass

class TestNamespaceLength(BaseTest):
    async def test_ns_length(self, db: MoonrakerDatabase):
        expected = len(TEST_DB["planets"])
        fut = db.ns_length("planets")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result == expected

    async def test_ns_length_invalid(self, db: MoonrakerDatabase):
        with pytest.raises(ServerError):
            fut = db.ns_length("invalid")
            await fut

class TestNamespaceLengthThreaded(ThreadedTest, TestNamespaceLength):
    pass

class TestNamespaceKeys(BaseTest):
    async def test_ns_keys(self, db: MoonrakerDatabase):
        expected = list(TEST_DB["planets"].keys())
        fut = db.ns_keys("planets")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result == sorted(expected)

    async def test_ns_keys_invalid(self, db: MoonrakerDatabase):
        with pytest.raises(ServerError):
            fut = db.ns_keys("invalid")
            await fut

class TestNamespaceKeysThreaded(ThreadedTest, TestNamespaceKeys):
    pass

class TestNamespaceValues(BaseTest):
    async def test_ns_values(self, db: MoonrakerDatabase):
        expected = [i[1] for i in sorted(TEST_DB["planets"].items(),
                    key=lambda d: d[0])]
        fut = db.ns_values("planets")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result == expected

    async def test_ns_values_invalid(self, db: MoonrakerDatabase):
        with pytest.raises(ServerError):
            fut = db.ns_values("invalid")
            await fut

class TestNamespaceValuesThreaded(ThreadedTest, TestNamespaceValues):
    pass

class TestNamespaceItems(BaseTest):
    async def test_ns_items(self, db: MoonrakerDatabase):
        expected = sorted(TEST_DB["planets"].items(), key=lambda d: d[0])
        fut = db.ns_items("planets")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result == expected

    async def test_ns_items_invalid(self, db: MoonrakerDatabase):
        with pytest.raises(ServerError):
            fut = db.ns_items("invalid")
            await fut

class TestNamespaceItemsThreaded(ThreadedTest, TestNamespaceItems):
    pass

class TestNamespaceContains(BaseTest):
    async def test_ns_contains_record(self, db: MoonrakerDatabase):
        fut = db.ns_contains("planets", "venus")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result is True

    async def test_ns_not_contains_record(self, db: MoonrakerDatabase):
        fut = db.ns_contains("planets", "mercury")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result is False

    async def test_ns_contains_nested(self, db: MoonrakerDatabase):
        fut = db.ns_contains("automobiles", "ford.f-series.f350.platinum")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result is True

    async def test_ns_not_contains_nested(self, db: MoonrakerDatabase):
        fut = db.ns_contains("automobiles", "ford.f-series.f250.fx4")
        result = check_future(fut, db)
        if isawaitable(result):
            result = await result
        assert result is False

    async def test_ns_contains_invalid(self, db: MoonrakerDatabase):
        with pytest.raises(ServerError):
            fut = db.ns_contains("invalid", "nokey")
            await fut

class TestNamespaceConainsThreaded(ThreadedTest, TestNamespaceContains):
    pass

class WrapperTest(BaseTest):
    @pytest.fixture(scope="class")
    def wrapped(self,
                request: pytest.FixtureRequest,
                db: MoonrakerDatabase
                ) -> NamespaceWrapper:
        parse = not request.cls.__name__.endswith("NoParse")
        return db.wrap_namespace("planets", parse_keys=parse)

@pytest.mark.asyncio
class WrapperTestThreaded:
    @pytest_asyncio.fixture(scope="class")
    async def wrapped(self,
                      request: pytest.FixtureRequest,
                      base_server: Server
                      ) -> AsyncIterator[MoonrakerDatabase]:
        base_server.load_components()
        db: MoonrakerDatabase = base_server.lookup_component("database")
        for ns, record in TEST_DB.items():
            for record_name, value in record.items():
                db.insert_item(ns, record_name, value)
        parse = not request.cls.__name__.endswith("NoParse")
        wrapped = db.wrap_namespace("planets", parse_keys=parse)
        await base_server.server_init(False)
        await base_server.start_server(False)
        yield wrapped
        await base_server._stop_server("terminate")

    async def test_asdict(self, wrapped: NamespaceWrapper):
        with pytest.raises(ServerError):
            wrapped.as_dict()

    async def test_contains_magic(self, wrapped: NamespaceWrapper):
        with pytest.raises(ServerError):
            "earth" in wrapped


class TestNamespaceWrapper(WrapperTest):
    async def test_wrapped_insert(self, wrapped: NamespaceWrapper):
        expected = copy.deepcopy(TEST_DB["planets"])
        expected["oses"] = TEST_RECORD
        wrapped.insert("oses", TEST_RECORD)
        fut = wrapped.db.get_item(wrapped.namespace)
        result = check_future(fut, wrapped.db)
        if isawaitable(result):
            result = await result
        assert result == expected

    async def test_wrapped_get(self, wrapped: NamespaceWrapper):
        fut = wrapped.get("oses")
        result = check_future(fut, wrapped.db)
        if isawaitable(result):
            result = await result
        assert result == TEST_RECORD

    async def test_wrapped_delete(self, wrapped: NamespaceWrapper):
        del_fut = wrapped.delete("oses")
        del_result = check_future(del_fut, wrapped.db)
        if isawaitable(del_result):
            del_result = await del_result
        fut = wrapped.db.get_item(wrapped.namespace)
        result = check_future(fut, wrapped.db)
        if isawaitable(result):
            result = await result
        assert (
            del_result == TEST_RECORD and
            result == TEST_DB["planets"]
        )

    async def test_wrapped_nested_insert(self, wrapped: NamespaceWrapper):
        expected = copy.deepcopy(TEST_DB["planets"])
        if wrapped.parse_keys:
            expected["oses"] = {"nested": TEST_RECORD}
        else:
            expected["oses.nested"] = TEST_RECORD
        wrapped.insert("oses.nested", TEST_RECORD)
        fut = wrapped.db.get_item(wrapped.namespace)
        result = check_future(fut, wrapped.db)
        if isawaitable(result):
            result = await result
        assert result == expected

    async def test_nested_get(self, wrapped: NamespaceWrapper):
        fut = wrapped.get("oses.nested")
        result = check_future(fut, wrapped.db)
        if isawaitable(result):
            result = await result
        assert result == TEST_RECORD

    async def test_wrapped_nested_delete(self, wrapped: NamespaceWrapper):
        del_fut = wrapped.delete("oses.nested")
        del_result = check_future(del_fut, wrapped.db)
        if isawaitable(del_result):
            del_result = await del_result
        fut = wrapped.db.get_item(wrapped.namespace)
        result = check_future(fut, wrapped.db)
        if isawaitable(result):
            result = await result
        assert (
            del_result == TEST_RECORD and
            result == TEST_DB["planets"]
        )

    async def test_update_child(self, wrapped: NamespaceWrapper):
        expected = copy.deepcopy(TEST_DB["planets"])
        expected["pluto"] = {"type": "dwarf"}
        wrapped.update_child("pluto", {"type": "dwarf"})
        fut = wrapped.db.get_item(wrapped.namespace)
        result = check_future(fut, wrapped.db)
        if isawaitable(result):
            result = await result
        assert result == expected

    async def test_update_child_nested(self, wrapped: NamespaceWrapper):
        expected = copy.deepcopy(TEST_DB["planets"])
        expected["pluto"] = {"type": "planet!"}
        wrapped.update_child("pluto.type", "planet!")
        fut = wrapped.db.get_item(wrapped.namespace)
        result = check_future(fut, wrapped.db)
        if isawaitable(result):
            result = await result
        assert result == expected

    async def test_update(self, wrapped: NamespaceWrapper):
        expected = copy.deepcopy(TEST_DB["planets"])
        upval = {"pluto": "Don't unplanet me!", "caprica": {"bsg": True}}
        expected.update(upval)
        wrapped.update(upval)
        fut = wrapped.db.get_item(wrapped.namespace)
        result = check_future(fut, wrapped.db)
        if isawaitable(result):
            result = await result
        assert result == expected

    async def test_sync(self, wrapped: NamespaceWrapper):
        wrapped.sync(TEST_DB["planets"])
        fut = wrapped.db.get_item(wrapped.namespace)
        result = check_future(fut, wrapped.db)
        if isawaitable(result):
            result = await result
        assert result == TEST_DB["planets"]

    async def test_insert_batch(self, wrapped: NamespaceWrapper):
        expected = copy.deepcopy(TEST_DB["planets"])
        expected.update(TEST_RECORD)
        wrapped.insert_batch(TEST_RECORD)
        fut = wrapped.db.get_item(wrapped.namespace)
        result = check_future(fut, wrapped.db)
        if isawaitable(result):
            result = await result
        assert result == expected

    async def test_get_batch(self, wrapped: NamespaceWrapper):
        fut = wrapped.get_batch(list(TEST_RECORD.keys()))
        result = check_future(fut, wrapped.db)
        if isawaitable(result):
            result = await result
        assert result == TEST_RECORD

    async def test_move_batch(self, wrapped: NamespaceWrapper):
        expected = copy.deepcopy(TEST_DB["planets"])
        expected.update({f"{k}_moved": v for k, v in TEST_RECORD.items()})
        source_keys = list(TEST_RECORD.keys())
        dest_keys = [f"{key}_moved" for key in source_keys]
        wrapped.move_batch(source_keys, dest_keys)
        fut = wrapped.db.get_item(wrapped.namespace)
        result = check_future(fut, wrapped.db)
        if isawaitable(result):
            result = await result
        assert result == expected

    async def test_delete_batch(self, wrapped: NamespaceWrapper):
        expected = {f"{k}_moved": v for k, v in TEST_RECORD.items()}
        fut = wrapped.delete_batch(list(expected.keys()))
        result = check_future(fut, wrapped.db)
        if isawaitable(result):
            result = await result
        assert result == expected

    async def test_length(self, wrapped: NamespaceWrapper):
        fut = wrapped.length()
        result = check_future(fut, wrapped.db)
        if isawaitable(result):
            result = await result
        assert result == len(TEST_DB["planets"])

    async def test_asdict(self, wrapped: NamespaceWrapper):
        assert wrapped.as_dict() == TEST_DB["planets"]

    async def test_setitem_magic(self, wrapped: NamespaceWrapper):
        expected = copy.deepcopy(TEST_DB["planets"])
        expected["neptune"] = {"distance_from_sun": 2.8}
        wrapped["neptune"] = {"distance_from_sun": 2.8}
        fut = wrapped.db.get_item(wrapped.namespace)
        result = check_future(fut, wrapped.db)
        if isawaitable(result):
            result = await result
        assert result == expected

    async def test_getitem_magic(self, wrapped: NamespaceWrapper):
        fut = wrapped["neptune"]
        result = check_future(fut, wrapped.db)
        if isawaitable(result):
            result = await result
        assert result == {"distance_from_sun": 2.8}

    async def test_getitem_magic_error(self, wrapped: NamespaceWrapper):
        with pytest.raises(ServerError):
            fut = wrapped["orthos"]
            await fut

    async def test_del_magic(self, wrapped: NamespaceWrapper):
        del wrapped["neptune"]
        fut = wrapped.db.get_item(wrapped.namespace)
        result = check_future(fut, wrapped.db)
        if isawaitable(result):
            result = await result
        assert result == TEST_DB["planets"]

    async def test_contains_magic(self, wrapped: NamespaceWrapper):
        assert "earth" in wrapped

    async def test_contains(self, wrapped: NamespaceWrapper):
        fut = wrapped.contains("earth")
        result = check_future(fut, wrapped.db)
        if isawaitable(result):
            result = await result
        assert result is True

    async def test_contains_nested(self, wrapped: NamespaceWrapper):
        fut = wrapped.contains("jupiter.io")
        result = check_future(fut, wrapped.db)
        if isawaitable(result):
            result = await result
        assert result is wrapped.parse_keys

    async def test_keys_method(self, wrapped: NamespaceWrapper):
        fut = wrapped.keys()
        result = check_future(fut, wrapped.db)
        if isawaitable(result):
            result = await result
        assert result == sorted(TEST_DB["planets"].keys())

    async def test_values_method(self, wrapped: NamespaceWrapper):
        expected = [TEST_DB["planets"][key] for key in
                    sorted(TEST_DB["planets"].keys())]
        fut = wrapped.values()
        result = check_future(fut, wrapped.db)
        if isawaitable(result):
            result = await result
        assert result == expected

    async def test_items_method(self, wrapped: NamespaceWrapper):
        expected = sorted(TEST_DB["planets"].items(), key=lambda x: x[0])
        fut = wrapped.items()
        result = check_future(fut, wrapped.db)
        if isawaitable(result):
            result = await result
        assert result == expected

    async def test_pop(self, wrapped: NamespaceWrapper):
        fut = wrapped.pop("jupiter")
        result = check_future(fut, wrapped.db)
        if isawaitable(result):
            result = await result
        assert result == TEST_DB["planets"]["jupiter"]

    async def test_pop_error(self, wrapped: NamespaceWrapper):
        with pytest.raises(ServerError):
            fut = wrapped.pop("invalid_key")
            await fut

    async def test_pop_default(self, wrapped: NamespaceWrapper):
        fut = wrapped.pop("invalid_key", "default_value")
        result = check_future(fut, wrapped.db)
        if isawaitable(result):
            result = await result
        assert result == "default_value"

    async def test_clear(self, wrapped: NamespaceWrapper):
        wrapped.clear()
        fut = wrapped.db.get_item(wrapped.namespace)
        result = check_future(fut, wrapped.db)
        if isawaitable(result):
            result = await result
        assert result == {}

class TestNamespaceWrapperNoParse(TestNamespaceWrapper):
    async def test_update_child_nested(self, wrapped: NamespaceWrapper):
        with pytest.raises(ServerError):
            ret = wrapped.update_child("pluto.type", "planet")
            await ret

class TestNamespaceWrapperThreaded(WrapperTestThreaded, TestNamespaceWrapper):
    pass

class TestNamespaceWrapperThreadedNoParse(
    WrapperTestThreaded, TestNamespaceWrapperNoParse
):
    pass

def endpoint_result(req_args: Dict[str, Any], expected: Any) -> Dict[str, Any]:
    return {
        "namespace": req_args["namespace"],
        "key": req_args.get("key"),
        "value": expected
    }

@pytest.mark.asyncio
class EndpointTest:
    @pytest_asyncio.fixture(scope="class", autouse=True)
    async def server(self, base_server: Server) -> AsyncIterator[Server]:
        base_server.load_components()
        db: MoonrakerDatabase = base_server.lookup_component("database")
        for ns, record in TEST_DB.items():
            for record_name, value in record.items():
                db.insert_item(ns, record_name, value)
        db.register_local_namespace("planets")
        db.register_local_namespace("fruits", forbidden=True)
        await base_server.server_init(False)
        await base_server.start_server(False)
        yield base_server
        await base_server._stop_server("terminate")

    @pytest.fixture(scope="class")
    def db(self, server: Server) -> MoonrakerDatabase:
        return server.lookup_component("database")

class TestHttpEndpoints(EndpointTest):
    async def test_list_dbs(self, http_client: HttpClient):
        expected = list(TEST_DB.keys())
        expected.remove("fruits")
        expected.extend(["moonraker", "gcode_metadata"])
        ret = await http_client.get("/server/database/list")
        assert sorted(ret["result"]["namespaces"]) == sorted(expected)

    async def test_get_namespace(self, http_client: HttpClient):
        args = {"namespace": "automobiles"}
        ret = await http_client.get("/server/database/item", args)
        assert ret["result"] == endpoint_result(args, TEST_DB["automobiles"])

    async def test_get_namespace_not_exist(self, http_client: HttpClient):
        with pytest.raises(http_client.error, match="HTTP 404:"):
            args = {"namespace": "cities"}
            await http_client.get("/server/database/item", args)

    async def test_get_item(self, http_client: HttpClient):
        args = {"namespace": "automobiles", "key": "ford.mustang"}
        ret = await http_client.get("/server/database/item", args)
        assert ret["result"] == endpoint_result(args, "red")

    async def test_get_item_not_exist(self, http_client: HttpClient):
        with pytest.raises(http_client.error, match="HTTP 404:"):
            args = {"namespace": "automobiles", "key": "ford.mustang.year"}
            await http_client.get("/server/database/item", args)

    async def test_get_item_protected_ns(self, http_client: HttpClient):
        args = {"namespace": "planets", "key": "jupiter.gas_giant"}
        ret = await http_client.get("/server/database/item", args)
        assert ret["result"] == endpoint_result(args, True)

    async def test_get_item_forbidden_ns(self, http_client: HttpClient):
        with pytest.raises(http_client.error, match="HTTP 403:"):
            args = {"namespace": "fruits", "key": "apples"}
            await http_client.get("/server/database/item", args)

    async def test_post_item(self, http_client: HttpClient,
                             db: MoonrakerDatabase):
        args = {"namespace": "breakfast", "key": "cereal.sweet",
                "value": "Count Chocula"}
        ret = await http_client.post("/server/database/item", args)
        check = await db.get_item("breakfast", "cereal.sweet")
        assert ret["result"] == args and check == "Count Chocula"

    async def test_post_item_no_key(self, http_client: HttpClient):
        with pytest.raises(http_client.error, match="HTTP 400:"):
            args = {"namespace": "breakfast", "value": "pancakes"}
            await http_client.post("/server/database/item", args)

    async def test_post_item_no_value(self, http_client: HttpClient):
        with pytest.raises(http_client.error, match="HTTP 400:"):
            args = {"namespace": "breakfast", "key": "pancakes"}
            await http_client.post("/server/database/item", args)

    async def test_post_item_protected(self, http_client: HttpClient):
        with pytest.raises(http_client.error, match="HTTP 403:"):
            args = {"namespace": "planets", "key": "jupiter.gas_giant",
                    "value": "biggest"}
            await http_client.post("/server/database/item", args)

    async def test_post_item_forbidden(self, http_client: HttpClient):
        with pytest.raises(http_client.error, match="HTTP 403:"):
            args = {"namespace": "fruits", "key": "cherries.color",
                    "value": "red"}
            await http_client.post("/server/database/item", args)

    async def test_delete_item(self, http_client: HttpClient,
                               db: MoonrakerDatabase):
        args = {"namespace": "automobiles", "key": "ford.f-series.f150"}
        ret = await http_client.delete("/server/database/item", args)
        check = await db.get_item("automobiles", "ford.f-series")
        assert (
            ret["result"] == endpoint_result(args, [150, "black"])
            and check == {"f350": {"platinum": 10000}}
        )

    async def test_delete_item_drop(self, http_client: HttpClient,
                                    db: MoonrakerDatabase):
        args = {"namespace": "vegetables", "key": "tomato"}
        ret = await http_client.delete("/server/database/item", args)
        assert (
            ret["result"] == endpoint_result(args, "nope")
            and "vegetables" not in db.namespaces
        )

    async def test_delete_item_not_found(self, http_client: HttpClient):
        with pytest.raises(http_client.error, match="HTTP 404: Not Found"):
            args = {"namespace": "automobiles", "key": "ford.pinto"}
            await http_client.delete("/server/database/item", args)

class TestWebsocketEndpoints(EndpointTest):
    async def test_list_dbs(self, websocket_client: WebsocketClient):
        expected = list(TEST_DB.keys())
        expected.remove("fruits")
        expected.extend(["moonraker", "gcode_metadata"])
        ret = await websocket_client.request("server.database.list")
        assert sorted(ret["namespaces"]) == sorted(expected)

    async def test_get_namespace(self, websocket_client: WebsocketClient):
        args = {"namespace": "automobiles"}
        ret = await websocket_client.request("server.database.get_item", args)
        assert ret == endpoint_result(args, TEST_DB["automobiles"])

    async def test_get_namespace_not_exist(self,
                                           websocket_client: WebsocketClient):
        expected = "Namespace 'cities' not found"
        with pytest.raises(websocket_client.error, match=expected):
            args = {"namespace": "cities"}
            await websocket_client.request("server.database.get_item", args)

    async def test_get_item(self, websocket_client: WebsocketClient):
        args = {"namespace": "automobiles", "key": "ford.mustang"}
        ret = await websocket_client.request("server.database.get_item", args)
        assert ret == endpoint_result(args, "red")

    async def test_get_item_not_exist(self,
                                      websocket_client: WebsocketClient):
        expected = (
            "Key 'ford.mustang.year' in namespace 'automobiles' not found"
        )
        with pytest.raises(websocket_client.error, match=expected):
            args = {"namespace": "automobiles", "key": "ford.mustang.year"}
            await websocket_client.request("server.database.get_item", args)

    async def test_get_item_protected_ns(self,
                                         websocket_client: WebsocketClient):
        args = {"namespace": "planets", "key": "jupiter.gas_giant"}
        ret = await websocket_client.request("server.database.get_item", args)
        assert ret == endpoint_result(args, True)

    async def test_get_item_forbidden_ns(self,
                                         websocket_client: WebsocketClient):
        expected = "Read/Write access to namespace 'fruits' is forbidden"
        with pytest.raises(websocket_client.error, match=expected):
            args = {"namespace": "fruits", "key": "apples"}
            await websocket_client.request("server.database.get_item", args)

    async def test_post_item(self, websocket_client: WebsocketClient,
                             db: MoonrakerDatabase):
        args = {"namespace": "breakfast", "key": "cereal.sweet",
                "value": "Count Chocula"}
        ret = await websocket_client.request("server.database.post_item", args)
        check = await db.get_item("breakfast", "cereal.sweet")
        assert ret == args and check == "Count Chocula"

    async def test_post_item_no_key(self, websocket_client: WebsocketClient):
        expected = "No data for argument: key"
        with pytest.raises(websocket_client.error, match=expected):
            args = {"namespace": "breakfast", "value": "pancakes"}
            await websocket_client.request("server.database.post_item", args)

    async def test_post_item_no_value(self, websocket_client: WebsocketClient):
        expected = "No data for argument: value"
        with pytest.raises(websocket_client.error, match=expected):
            args = {"namespace": "breakfast", "key": "pancakes"}
            await websocket_client.request("server.database.post_item", args)

    async def test_post_item_protected(self,
                                       websocket_client: WebsocketClient):
        expected = "Write access to namespace 'planets' is forbidden"
        with pytest.raises(websocket_client.error, match=expected):
            args = {"namespace": "planets", "key": "jupiter.gas_giant",
                    "value": "biggest"}
            await websocket_client.request("server.database.post_item", args)

    async def test_post_item_forbidden(self,
                                       websocket_client: WebsocketClient):
        expected = "Read/Write access to namespace 'fruits' is forbidden"
        with pytest.raises(websocket_client.error, match=expected):
            args = {"namespace": "fruits", "key": "cherries.color",
                    "value": "red"}
            await websocket_client.request("server.database.post_item", args)

    async def test_delete_item(self, websocket_client: WebsocketClient,
                               db: MoonrakerDatabase):
        args = {"namespace": "automobiles", "key": "ford.f-series.f150"}
        ret = await websocket_client.request(
            "server.database.delete_item", args)
        check = await db.get_item("automobiles", "ford.f-series")
        assert (
            ret == endpoint_result(args, [150, "black"])
            and check == {"f350": {"platinum": 10000}}
        )

    async def test_delete_item_drop(self, websocket_client: WebsocketClient,
                                    db: MoonrakerDatabase):
        args = {"namespace": "vegetables", "key": "tomato"}
        ret = await websocket_client.request(
            "server.database.delete_item", args)
        assert (
            ret == endpoint_result(args, "nope")
            and "vegetables" not in db.namespaces
        )

    async def test_delete_item_not_found(self,
                                         websocket_client: WebsocketClient):
        expected = "Key 'ford.pinto' in namespace 'automobiles' not found"
        with pytest.raises(websocket_client.error, match=expected):
            args = {"namespace": "automobiles", "key": "ford.pinto"}
            await websocket_client.request("server.database.delete_item", args)

    async def test_invalid_key(self, websocket_client: WebsocketClient):
        expected = "Value for argument 'key' is an invalid type"
        with pytest.raises(websocket_client.error, match=expected):
            args = {"namespace": "planets", "key": {"ford": "pinto"}}
            await websocket_client.request("server.database.get_item", args)
