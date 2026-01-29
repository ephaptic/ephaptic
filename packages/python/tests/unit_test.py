import pytest
from ephaptic.ephaptic import Ephaptic, EphapticTarget, expose as global_expose
from ephaptic.decorators import META_KEY
import pydantic
from fastapi import FastAPI

def test_global_expose_picked_up():
    @global_expose
    def g_func():
        return 'global'
    
    app = FastAPI()
    eph = Ephaptic.from_app(app)

    assert 'g_func' in eph._exposed_functions
    assert eph._exposed_functions['g_func']() == 'global'

def test_expose_metadata_storage():
    app = FastAPI()
    eph = Ephaptic.from_app(app)

    @eph.expose(rate_limit='5/m')
    def limited(): ...

    meta = getattr(limited, META_KEY)
    assert meta['rate_limit'] == (5, 60)

def test_expose_name():
    app = FastAPI()
    eph = Ephaptic.from_app(app)

    @eph.expose(name='new_name')
    def old_name(): return 'ok'

    assert 'new_name' in eph._exposed_functions
    assert eph._exposed_functions['new_name']() == 'ok'

def test_expose_with_from_app():
    app = FastAPI()
    eph = Ephaptic.from_app(app)

    @eph.expose
    def my_func():
        return "hello"

    assert "my_func" in eph._exposed_functions
    assert eph._exposed_functions["my_func"]() == "hello"

def test_event_with_from_app():
    app = FastAPI()
    eph = Ephaptic.from_app(app)

    @eph.event
    class MyEvent(pydantic.BaseModel):
        message: str

    assert "MyEvent" in eph._exposed_events
    assert eph._exposed_events["MyEvent"] == MyEvent

def test_identity_loader_with_from_app():
    app = FastAPI()
    eph = Ephaptic.from_app(app)

    @eph.identity_loader
    def my_loader(token):
        return "user123"

    assert eph._identity_loader is not None
    assert eph._identity_loader("some_token") == "user123"

def test_to_method():
    app = FastAPI()
    eph = Ephaptic.from_app(app)
    target = eph.to("user1", "user2")
    assert isinstance(target, EphapticTarget)
    assert target.user_ids == ["user1", "user2"]

    target_list = eph.to(["user3", "user4"])
    assert isinstance(target_list, EphapticTarget)
    assert target_list.user_ids == ["user3", "user4"]

    target_mixed = eph.to("user5", ["user6", "user7"])
    assert isinstance(target_mixed, EphapticTarget)
    assert target_mixed.user_ids == ["user5", "user6", "user7"]