"""Training site definitions — 13th Legion field training locations."""

TRAINING_SITES = {
    "able": {
        "name": "Able",
        "nickname": "Camp's",
        "address": "1925 E FM 4, Cleburne, TX 76031",
        "maps": {
            "10k": "/static/maps/training-sites/Able_10000.pdf",
            "25k": "/static/maps/training-sites/Able_25000.pdf",
        },
    },
    "baker": {
        "name": "Baker",
        "nickname": "Trey's",
        "address": "1019 County Road 128, Stephenville, TX 76401",
        "maps": {
            "10k": "/static/maps/training-sites/Baker_10000.pdf",
            "10k_marked": "/static/maps/training-sites/Baker_10000_marked.pdf",
            "25k": "/static/maps/training-sites/Baker_25000.pdf",
        },
    },
    "charlie": {
        "name": "Charlie",
        "nickname": "Way Out West Shooting",
        "address": "16400 US-281, Santo, TX 76472",
        "maps": {
            "10k": "/static/maps/training-sites/Charlie_10000.pdf",
            "25k": "/static/maps/training-sites/Charlie_25000.pdf",
        },
    },
    "dog": {
        "name": "Dog",
        "nickname": "Richardson's",
        "address": "128 County Road 499, Hico, TX 76457",
        "maps": {
            "10k": "/static/maps/training-sites/Dog_10000.pdf",
            "25k": "/static/maps/training-sites/Dog_25000.pdf",
        },
    },
    "easy": {
        "name": "Easy",
        "nickname": "Camp's Parents'",
        "address": "6142 County Road 311, Grandview, TX 76050",
        "maps": {
            "25k": "/static/maps/training-sites/Easy_25000.pdf",
            "25k_marked": "/static/maps/training-sites/Easy_25000_marked.pdf",
        },
    },
}


def get_site(key: str) -> dict | None:
    return TRAINING_SITES.get(key)


def get_site_maps(key: str) -> list[dict]:
    """Return list of {label, url} for a training site's maps."""
    site = TRAINING_SITES.get(key)
    if not site:
        return []
    maps = []
    for scale_key, url in site["maps"].items():
        label = f"Training Site {site['name']} — "
        if scale_key == "10k":
            label += "1:10,000"
        elif scale_key == "10k_marked":
            label += "1:10,000 (Marked)"
        elif scale_key == "25k":
            label += "1:25,000"
        elif scale_key == "25k_marked":
            label += "1:25,000 (Marked)"
        maps.append({"label": label, "url": url})
    return maps
