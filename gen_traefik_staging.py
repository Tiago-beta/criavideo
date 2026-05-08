#!/usr/bin/env python3
"""Generate Traefik dynamic config for staging.criavideo.pro."""

bt = chr(96)

content = f"""http:
  routers:
    criavideo-staging-http:
      entryPoints:
        - http
      rule: "Host({bt}staging.criavideo.pro{bt})"
      service: criavideo-staging-svc
      middlewares:
        - criavideo-staging-redirect-https
    criavideo-staging-https:
      entryPoints:
        - https
      rule: "Host({bt}staging.criavideo.pro{bt})"
      service: criavideo-staging-svc
      tls:
        certresolver: letsencrypt
  middlewares:
    criavideo-staging-redirect-https:
      redirectScheme:
        scheme: https
        permanent: true
  services:
    criavideo-staging-svc:
      loadBalancer:
        servers:
          - url: http://host.docker.internal:8003
"""

with open("/data/coolify/proxy/dynamic/criavideo-staging.yaml", "w", encoding="utf-8") as f:
    f.write(content)
print("Traefik staging config written successfully")