"""SQLAlchemy ORM models.

IMPORTANT: This module is imported by ``migrations/env.py`` so that
``Base.metadata`` is populated before Alembic autogenerate runs. Every
model defined under ``app/models/`` must be re-exported here so importing
``app.models`` is enough to register every mapper.
"""

from app.models.category_mapping_rule import CategoryMappingRule
from app.models.dataset_version import DatasetVersion
from app.models.persona_region_weight import PersonaRegionWeight
from app.models.place_normalized import PlaceNormalized
from app.models.place_raw import PlaceRawKakao
from app.models.real_activity_agg import RealActivityAgg
from app.models.region import RegionMaster
from app.models.region_feature import RegionFeature
from app.models.spot_seed import SpotSeedDataset

__all__ = [
    "CategoryMappingRule",
    "DatasetVersion",
    "PersonaRegionWeight",
    "PlaceNormalized",
    "PlaceRawKakao",
    "RealActivityAgg",
    "RegionFeature",
    "RegionMaster",
    "SpotSeedDataset",
]
