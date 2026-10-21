storage "file" {
  path = "/openbao/data"
}

# HTTPS listener for external clients.
# Required by Percona Server for MongoDB: its vault interface always uses TLS.
listener "tcp" {
  address         = "0.0.0.0:8200"
  tls_disable     = "false"
  tls_cert_file   = "/openbao/tls/server.crt"
  tls_key_file    = "/openbao/tls/server.key"
  tls_min_version = "tls12"
}

# Plain HTTP listener bound to loopback only: used for container-local health
# checks and the bao CLI (busybox wget cannot skip certificate verification).
# Port 8201 is not published to the host and must not be used by other containers.
listener "tcp" {
  address     = "127.0.0.1:8201"
  tls_disable = "true"
}

ui = true
api_addr = "https://openbao:8200"

