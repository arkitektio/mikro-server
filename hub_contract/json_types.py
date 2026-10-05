"""The shape of a config document: what YAML holds."""

type JSON = str | int | float | bool | None | list[JSON] | dict[str, JSON]
