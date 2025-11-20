from rest_framework.throttling import AnonRateThrottle


class HighLimitAnonRateThrottle(AnonRateThrottle):
    # Custom throttle with high limit (for login, logout, token refresh)
    rate = "180/minute"
