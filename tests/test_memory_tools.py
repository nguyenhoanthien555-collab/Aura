import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from memory.models import Base
from memory.profile import ProfileStore
from tools.builtins.memory_tools import RememberFactTool, ForgetFactTool
from tools.factory import build_registry


from sqlalchemy.pool import StaticPool


@pytest.fixture
def profile_store():
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(test_engine)
    TestingSessionLocal = sessionmaker(bind=test_engine)
    session = TestingSessionLocal()
    store = ProfileStore(session=session)
    yield store
    session.close()


def test_remember_fact_success(profile_store):
    tool = RememberFactTool(profile_store=profile_store)
    res = tool.execute(key="favorite_drink", value="Bac Xiu Coffee", category="preference")
    assert res.ok is True
    assert "favorite_drink" in res.output

    # Verify fact stored
    val = profile_store.get("favorite_drink")
    assert val == "Bac Xiu Coffee"


def test_remember_fact_privacy_refusal(profile_store):
    tool = RememberFactTool(profile_store=profile_store)
    # Refuse API Key
    res = tool.execute(key="api_key", value="AIzaSyD-7389274928374923749238472938472")
    assert res.ok is False
    assert "Privacy refusal" in res.error

    # Refuse Password
    res2 = tool.execute(key="my_pass", value="password is: SuperSecret123!")
    assert res2.ok is False
    assert "Privacy refusal" in res2.error

    # No facts stored
    assert len(profile_store.all()) == 0


def test_forget_fact_success(profile_store):
    profile_store.remember("city", "Da Nang")
    assert profile_store.get("city") == "Da Nang"

    tool = ForgetFactTool(profile_store=profile_store)
    res = tool.execute(key="city")
    assert res.ok is True
    assert "Successfully erased" in res.output
    assert profile_store.get("city") == ""


def test_memory_tools_in_registry():
    reg = build_registry()
    assert "remember_fact" in reg.names()
    assert "forget_fact" in reg.names()
    assert reg.get("remember_fact").capability == "memory.remember"
    assert reg.get("forget_fact").capability == "memory.forget"
