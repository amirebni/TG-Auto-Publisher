"""Decide which destinations a content type goes to."""


def destinations_for(source_cfg, kind):
    routes = source_cfg.get("routes") or {}

    if kind in routes:
        value = routes[kind]
    else:
        value = routes.get("default", source_cfg.get("destinations", []))

    if isinstance(value, (str, int)):
        value = [value]

    # de-duplicate, keep order
    result = []
    for item in value or []:
        if item not in result:
            result.append(item)

    return result
