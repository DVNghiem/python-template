# -*- coding: utf-8 -*-
from functools import reduce
from typing import Any, Dict, Generic, Optional, Type, TypeVar
from sqlalchemy import Select, select, and_, desc, asc, between
from sqlalchemy.ext.asyncio import (
	AsyncSession,
)
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func

Base = declarative_base()
ModelType = TypeVar('ModelType', bound=Base)  # type: ignore


class PostgresRepository(Generic[ModelType]):
	"""Base class for data repositories."""

	def __init__(self, model: Type[ModelType], db_session: AsyncSession):
		self.session = db_session
		self.model_class: Type[ModelType] = model

	async def create(self, attributes: Optional[dict[str, Any]] = None) -> ModelType:
		"""
		Creates the model instance.

		:param attributes: The attributes to create the model with.
		:return: The created model instance.
		"""
		if attributes is None:
			attributes = {}
		model = self.model_class(**attributes)  # type: ignore
		self.session.add(model)
		return model

	async def get_all(
		self,
		skip: int = 0,
		limit: int = 100,
		join_: set[str] | None = None,
		where: Optional[dict] = None,
		order_by: tuple[str, str] | None = None,
	) -> list[ModelType]:
		"""
		Returns a list of model instances.

		:param skip: The number of records to skip.
		:param limit: The number of record to return.
		:param join_: The joins to make.
		:param where: The conditions for the WHERE clause.
		:return: A list of model instances.
		"""
		query = self._query(join_)
		query = query.offset(skip).limit(limit)

		if where is not None:
			conditions = []
			for k, v in where.items():
				if isinstance(v, dict) and '$gt' in v and '$lt' in v:
					conditions.append(between(getattr(self.model_class, k), v['$gt'], v['$lt']))
				else:
					conditions.append(getattr(self.model_class, k) == v)
			query = query.where(and_(*conditions))

		if order_by is not None:
			column, direction = order_by
			if direction.lower() == 'desc':
				query = query.order_by(desc(getattr(self.model_class, column)))
			else:
				query = query.order_by(asc(getattr(self.model_class, column)))

		if join_ is not None:
			return await self.all_unique(query)
		return await self._all(query)

	async def get_by(
		self,
		field: str,
		value: Any,
		join_: set[str] | None = None,
		unique: bool = False,
	) -> ModelType | list[ModelType] | None:
		"""
		Returns the model instance matching the field and value.

		:param field: The field to match.
		:param value: The value to match.
		:param join_: The joins to make.
		:return: The model instance.
		"""
		query = self._query(join_)
		query = await self._get_by(query, field, value)

		if join_ is not None:
			return await self.all_unique(query)
		if unique:
			return await self._one(query)

		return await self._all(query)

	async def delete(self, model: ModelType) -> None:
		"""
		Deletes the model.

		:param model: The model to delete.
		:return: None
		"""
		await self.session.delete(model)

	async def update(self, model: ModelType, attributes: dict[str, Any]) -> ModelType:
		"""
		Updates the model.

		:param model: The model to update.
		:param attributes: The attributes to update the model with.
		:return: The updated model instance.
		"""
		for key, value in attributes.items():
			if hasattr(model, key):
				setattr(model, key, value)

		self.session.add(model)
		await self.session.commit()

		return model

	def _query(
		self,
		join_: set[str] | None = None,
		order_: dict | None = None,
	) -> Select:
		"""
		Returns a callable that can be used to query the model.

		:param join_: The joins to make.
		:param order_: The order of the results. (e.g desc, asc)
		:return: A callable that can be used to query the model.
		"""
		query = select(self.model_class)
		query = self._maybe_join(query, join_)
		query = self._maybe_ordered(query, order_)

		return query

	async def _all(self, query: Select) -> list[ModelType]:
		"""
		Returns all results from the query.

		:param query: The query to execute.
		:return: A list of model instances.
		"""
		query = await self.session.scalars(query)
		return query.all()

	async def all_unique(self, query: Select) -> list[ModelType]:
		result = await self.session.execute(query)
		return result.unique().scalars().all()

	async def _first(self, query: Select) -> ModelType | None:
		"""
		Returns the first result from the query.

		:param query: The query to execute.
		:return: The first model instance.
		"""
		query = await self.session.scalars(query)
		return query.first()

	async def _one_or_none(self, query: Select) -> ModelType | None:
		"""Returns the first result from the query or None."""
		query = await self.session.scalars(query)
		return query.one_or_none()

	async def _one(self, query: Select) -> ModelType:
		"""
		Returns the first result from the query or raises NoResultFound.

		:param query: The query to execute.
		:return: The first model instance.
		"""
		query = await self.session.scalars(query)
		return query.one()

	async def _count(self, query: Select) -> int:
		"""
		Returns the count of the records.

		:param query: The query to execute.
		"""
		query = query.subquery()
		query = await self.session.scalars(select(func.count()).select_from(query))
		return query.one()

	async def _sort_by(
		self,
		query: Select,
		sort_by: str,
		order: str | None = 'asc',
		model: Type[ModelType] | None = None,
		case_insensitive: bool = False,
	) -> Select:
		"""
		Returns the query sorted by the given column.

		:param query: The query to sort.
		:param sort_by: The column to sort by.
		:param order: The order to sort by.
		:param model: The model to sort.
		:param case_insensitive: Whether to sort case insensitively.
		:return: The sorted query.
		"""
		model = model or self.model_class

		order_column = None

		if case_insensitive:
			order_column = func.lower(getattr(model, sort_by))
		else:
			order_column = getattr(model, sort_by)

		if order == 'desc':
			return query.order_by(order_column.desc())

		return query.order_by(order_column.asc())

	async def _get_by(self, query: Select, field: str, value: Any) -> Select:
		"""
		Returns the query filtered by the given column.

		:param query: The query to filter.
		:param field: The column to filter by.
		:param value: The value to filter by.
		:return: The filtered query.
		"""
		return query.where(getattr(self.model_class, field) == value)

	def _maybe_join(self, query: Select, join_: set[str] | None = None) -> Select:
		"""
		Returns the query with the given joins.

		:param query: The query to join.
		:param join_: The joins to make.
		:return: The query with the given joins.
		"""
		if not join_:
			return query

		if not isinstance(join_, set):
			raise TypeError('join_ must be a set')

		return reduce(self._add_join_to_query, join_, query)  # type: ignore

	def _maybe_ordered(self, query: Select, order_: dict | None = None) -> Select:
		"""
		Returns the query ordered by the given column.

		:param query: The query to order.
		:param order_: The order to make.
		:return: The query ordered by the given column.
		"""
		if order_:
			if order_['asc']:
				for order in order_['asc']:
					query = query.order_by(getattr(self.model_class, order).asc())
			else:
				for order in order_['desc']:
					query = query.order_by(getattr(self.model_class, order).desc())

		return query

	def _add_join_to_query(self, query: Select, join_: str) -> Select:
		"""
		Returns the query with the given join.

		:param query: The query to join.
		:param join_: The join to make.
		:return: The query with the given join.
		"""
		return getattr(self, '_join_' + join_)(query)


class Model(Base):  # type: ignore
	__abstract__ = True
	__table_args__ = {'extend_existing': True}

	@property
	def as_dict(self) -> Dict:
		return {c.name: getattr(self, c.name) for c in self.__table__.columns}