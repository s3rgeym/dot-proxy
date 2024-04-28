# README

[![PyPI - Version](https://img.shields.io/pypi/v/dot-proxy)]() [![PyPI - License](https://img.shields.io/pypi/l/dot-proxy)]() [![PyPI - Downloads](https://img.shields.io/pypi/dm/dot-proxy)]()

DNS over TLS Proxy Server for Non-Systemd Distros.

## Install and Run

Via Docker Compose:

```bash
# clone project
$ git clone https://github.com/s3rgeym/dot-proxy
$ cd dot-proxy

# edit .env to specify custom dns

# run proxy
$ docker-compose up -d

# test
$ dig www.linux.org.ru @127.0.0.52

; <<>> DiG 9.18.26 <<>> www.linux.org.ru @127.0.0.52
;; global options: +cmd
;; Got answer:
;; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 48430
;; flags: qr rd ra; QUERY: 1, ANSWER: 1, AUTHORITY: 0, ADDITIONAL: 1

;; OPT PSEUDOSECTION:
; EDNS: version: 0, flags:; udp: 1232
; PAD: (403 bytes)
;; QUESTION SECTION:
;www.linux.org.ru.              IN      A

;; ANSWER SECTION:
www.linux.org.ru.       2072    IN      A       178.248.233.6

;; Query time: 100 msec
;; SERVER: 127.0.0.52#53(127.0.0.52) (UDP)
;; WHEN: Sun Apr 28 05:55:31 MSK 2024
;; MSG SIZE  rcvd: 468


# To view logs
$ docker-compose logs -f
```

Using PIP:

```bash
pip install dot-proxy
dot-proxy -h
```


## Configure System DNS

Configure Network Manager:

`/etc/NetworkManager/conf.d/dns.conf`:
```
[main]
dns=none
```

Configure DNS Resolver:

`/etc/resolv.conf`:
```conf
nameserver 127.0.0.52
```

Test:

```bash
$ dig www.linux.org.ru | grep -i server
;; SERVER: 127.0.0.52#53(127.0.0.52) (UDP)
```
