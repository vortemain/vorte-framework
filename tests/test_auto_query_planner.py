import pytest
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, select, ForeignKey
from sqlalchemy.orm import declarative_base, relationship

from vorte.modules.database.planner import active_relations, QueryPlanner
from vorte.core.router import infer_relations


Base = declarative_base()


class Profile(Base):
    __tablename__ = "profiles"
    id = Column(Integer, primary_key=True)
    bio = Column(String)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    name = Column(String)
    profile_id = Column(Integer, ForeignKey("profiles.id"))
    
    # SQLAlchemy relationship
    profile = relationship("Profile")


class ProfileResponse(BaseModel):
    bio: str


class UserResponse(BaseModel):
    id: int
    name: str
    profile: ProfileResponse
    posts: list[int]


def test_infer_relations():
    """Test that infer_relations extracts all nested relationship fields from a Pydantic model."""
    relations = infer_relations(UserResponse)
    
    assert "profile" in relations
    assert "id" not in relations
    assert "name" not in relations


def test_query_planner_apply_active():
    """Test that QueryPlanner correctly filters and applies valid relations from context."""
    planner = QueryPlanner()
    
    # Set context with inferred fields + some invalid ones
    inferred_fields = ("id", "name", "profile", "posts", "non_existent")
    token = active_relations.set(inferred_fields)
    
    try:
        stmt = select(User)
        stmt = planner.apply_active(stmt, User)
        
        # Verify which selectinload options were added
        # We can inspect the _with_options property of the statement
        options = stmt._with_options
        
        # Only 'profile' should have been added as an option
        # because 'id', 'name', 'posts', 'non_existent' are not SQLAlchemy relationships on User
        assert len(options) == 1
        
        # We can verify it was tracked in stats
        assert planner.stats.get("profile") == 1
        assert planner.stats.get("posts") is None
        
    finally:
        active_relations.reset(token)
