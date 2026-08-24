from server.api.schemas.common import ApiModel


class ModelItem(ApiModel):
    model_id: str
    name: str


class ModelsResponse(ApiModel):
    items: list[ModelItem]

