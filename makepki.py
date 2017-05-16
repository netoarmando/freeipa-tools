#!/usr/bin/python3
#
# Copyright (c) 2015-2017, Jan Cholasta <jcholast@redhat.com>
#
# Permission to use, copy, modify, and/or distribute this software for any
# purpose with or without fee is hereby granted, provided that the above
# copyright notice and this permission notice appear in all copies.
#
# THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
# WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
# MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
# ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
# WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
# ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
# OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

import collections
import datetime
import itertools
import os
import os.path

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

DOMAIN = 'example.com'
SERVER1 = 'server1.example.com'
SERVER2 = 'server2.example.com'
CLIENT = 'client.example.com'
DIR = os.path.abspath('pki')
PASSWORD = 'password'

DAY = datetime.timedelta(days=1)
YEAR = 365 * DAY

CertInfo = collections.namedtuple('CertInfo', 'nick key cert counter')


def profile_ca(builder, ca_nick):
    now = datetime.datetime.utcnow()

    builder = builder.not_valid_before(now)
    builder = builder.not_valid_after(now + 10 * YEAR)

    crl_uri = 'file://{}.crl'.format(os.path.join(DIR, ca_nick))

    builder = builder.add_extension(
        x509.KeyUsage(
            digital_signature=True,
            content_commitment=True,
            key_encipherment=False,
            data_encipherment=False,
            key_agreement=False,
            key_cert_sign=True,
            crl_sign=True,
            encipher_only=False,
            decipher_only=False,
        ),
        critical=True,
    )
    builder = builder.add_extension(
        x509.BasicConstraints(ca=True, path_length=None),
        critical=True,
    )
    builder = builder.add_extension(
        x509.CRLDistributionPoints([
                x509.DistributionPoint(
                    full_name=[x509.UniformResourceIdentifier(crl_uri)],
                    relative_name=None,
                    crl_issuer=None,
                    reasons=None,
                ),
        ]),
        critical=False,
    )

    return builder


def profile_server(builder, ca_nick,
                   warp=datetime.timedelta(days=0), dns_name=None):
    now = datetime.datetime.utcnow() + warp

    builder = builder.not_valid_before(now)
    builder = builder.not_valid_after(now + YEAR)

    crl_uri = 'file://{}.crl'.format(os.path.join(DIR, ca_nick))

    builder = builder.add_extension(
        x509.CRLDistributionPoints([
                x509.DistributionPoint(
                    full_name=[x509.UniformResourceIdentifier(crl_uri)],
                    relative_name=None,
                    crl_issuer=None,
                    reasons=None,
                ),
        ]),
        critical=False,
    )

    if dns_name is not None:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(dns_name)]),
            critical=False,
        )

    return builder


def gen_cert(profile, nick_base, subject, ca=None, **kwargs):
    key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    public_key = key.public_key()

    counter = itertools.count(1)

    if ca is not None:
        ca_nick, ca_key, ca_cert, ca_counter = ca
        nick = os.path.join(ca_nick, nick_base)
        issuer = ca_cert.subject
    else:
        nick = ca_nick = nick_base
        ca_key = key
        ca_counter = counter
        issuer = subject

    serial = next(ca_counter)

    builder = x509.CertificateBuilder()
    builder = builder.serial_number(serial)
    builder = builder.issuer_name(issuer)
    builder = builder.subject_name(subject)
    builder = builder.public_key(public_key)

    builder = profile(builder, ca_nick, **kwargs)

    cert = builder.sign(
        private_key=ca_key,
        algorithm=hashes.SHA256(),
        backend=default_backend(),
    )

    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(PASSWORD.encode()),
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)

    try:
        os.makedirs(os.path.dirname(os.path.join(DIR, nick)))
    except FileExistsError:
        pass
    with open(os.path.join(DIR, nick + '.key'), 'wb') as f:
        f.write(key_pem)
    with open(os.path.join(DIR, nick + '.crt'), 'wb') as f:
        f.write(cert_pem)

    return CertInfo(nick, key, cert, counter)


def revoke_cert(ca, serial):
    now = datetime.datetime.utcnow()

    crl_builder = x509.CertificateRevocationListBuilder()
    crl_builder = crl_builder.issuer_name(ca.cert.subject)
    crl_builder = crl_builder.last_update(now)
    crl_builder = crl_builder.next_update(now + DAY)

    crl_filename = os.path.join(DIR, ca.nick + '.crl')

    try:
        f = open(crl_filename, 'rb')
    except FileNotFoundError:
        pass
    else:
        with f:
            crl_pem = f.read()

        crl = x509.load_pem_x509_crl(crl_pem, default_backend())

        for revoked_cert in crl:
            crl_builder = crl_builder.add_revoked_certificate(revoked_cert)

    builder = x509.RevokedCertificateBuilder()
    builder = builder.serial_number(serial)
    builder = builder.revocation_date(now)

    revoked_cert = builder.build(default_backend())

    crl_builder = crl_builder.add_revoked_certificate(revoked_cert)

    crl = crl_builder.sign(
        private_key=ca.key,
        algorithm=hashes.SHA256(),
        backend=default_backend(),
    )

    crl_pem = crl.public_bytes(serialization.Encoding.PEM)

    with open(crl_filename, 'wb') as f:
        f.write(crl_pem)


def gen_server_certs(nick_base, hostname, org, ca=None):
    gen_cert(profile_server, nick_base, x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, org), x509.NameAttribute(NameOID.COMMON_NAME, hostname)]), ca)
    gen_cert(profile_server, nick_base + '-badname', x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, org), x509.NameAttribute(NameOID.COMMON_NAME, 'not-' + hostname)]), ca)
    gen_cert(profile_server, nick_base + '-altname', x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, org), x509.NameAttribute(NameOID.COMMON_NAME, 'alt-' + hostname)]), ca, dns_name=hostname)
    gen_cert(profile_server, nick_base + '-expired', x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, org), x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, 'Expired'), x509.NameAttribute(NameOID.COMMON_NAME, hostname)]), ca, warp=-2 * YEAR)
    gen_cert(profile_server, nick_base + '-badusage', x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, org), x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, 'Bad Usage'), x509.NameAttribute(NameOID.COMMON_NAME, hostname)]), ca)
    revoked = gen_cert(profile_server, nick_base + '-revoked', x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, org), x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, 'Revoked'), x509.NameAttribute(NameOID.COMMON_NAME, hostname)]), ca)
    revoke_cert(ca, revoked.cert.serial_number)


def gen_subtree(nick_base, org, ca=None):
    subca = gen_cert(profile_ca, nick_base, x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, org), x509.NameAttribute(NameOID.COMMON_NAME, 'CA')]), ca)
    gen_cert(profile_server, 'wildcard', x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, org), x509.NameAttribute(NameOID.COMMON_NAME, '*.' + DOMAIN)]), subca)
    gen_server_certs('server', SERVER1, org, subca)
    gen_server_certs('replica', SERVER2, org, subca)
    gen_server_certs('client', CLIENT, org, subca)
    return subca


def main():
    gen_cert(profile_server, 'server-selfsign', x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'Self-signed'), x509.NameAttribute(NameOID.COMMON_NAME, SERVER1)]))
    gen_cert(profile_server, 'replica-selfsign', x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'Self-signed'), x509.NameAttribute(NameOID.COMMON_NAME, SERVER2)]))
    ca1 = gen_subtree('ca1', 'Example Organization')
    gen_subtree('subca', 'Subsidiary Example Organization', ca1)
    gen_subtree('ca2', 'Other Example Organization')
    ca3 = gen_subtree('ca3', 'Unknown Organization')
    os.unlink(os.path.join(DIR, ca3.nick + '.key'))
    os.unlink(os.path.join(DIR, ca3.nick + '.crt'))


if __name__ == '__main__':
    main()
