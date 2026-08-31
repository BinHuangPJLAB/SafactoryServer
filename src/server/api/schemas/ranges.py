from server.api.schemas.common import ApiModel


class RangeItem(ApiModel):
    range_id: str
    description: str
