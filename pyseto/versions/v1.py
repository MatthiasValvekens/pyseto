import hashlib
import hmac
from secrets import token_bytes
from typing import Union

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..exceptions import DecryptError, EncryptError, SignError, VerifyError
from ..key_interface import KeyInterface
from ..utils import base64url_encode, pae


class V1Local(KeyInterface):
    """
    The key object for v1.local.
    """

    def __init__(self, key: Union[str, bytes]):

        super().__init__(1, "local", key)
        return

    def encrypt(
        self,
        payload: bytes,
        footer: bytes = b"",
        implicit_assertion: bytes = b"",
        nonce: bytes = b"",
    ) -> bytes:

        n = self._get_nonce(payload, nonce)
        e = HKDF(
            algorithm=hashes.SHA384(),
            length=32,
            salt=n[0:16],
            info=b"paseto-encryption-key",
        )
        a = HKDF(
            algorithm=hashes.SHA384(),
            length=32,
            salt=n[0:16],
            info=b"paseto-auth-key-for-aead",
        )
        ek = e.derive(self._key)
        ak = a.derive(self._key)

        try:
            c = (
                Cipher(algorithms.AES(ek), modes.CTR(n[16:]))
                .encryptor()
                .update(payload)
            )
            pre_auth = pae([self.header, n, c, footer])
            t = hmac.new(ak, pre_auth, hashlib.sha384).digest()
            token = self._header + base64url_encode(n + c + t)
            if footer:
                token += b"." + base64url_encode(footer)
            return token
        except Exception as err:
            raise EncryptError("Failed to encrypt.") from err

    def decrypt(
        self, payload: bytes, footer: bytes = b"", implicit_assertion: bytes = b""
    ) -> bytes:

        n = payload[0:32]
        t = payload[-48:]
        c = payload[32 : len(payload) - 48]
        e = HKDF(
            algorithm=hashes.SHA384(),
            length=32,
            salt=n[0:16],
            info=b"paseto-encryption-key",
        )
        a = HKDF(
            algorithm=hashes.SHA384(),
            length=32,
            salt=n[0:16],
            info=b"paseto-auth-key-for-aead",
        )
        ek = e.derive(self._key)
        ak = a.derive(self._key)

        pre_auth = pae([self.header, n, c, footer])
        t2 = hmac.new(ak, pre_auth, hashlib.sha384).digest()
        if t != t2:
            raise DecryptError("Failed to decrypt.")

        try:
            return Cipher(algorithms.AES(ek), modes.CTR(n[16:])).decryptor().update(c)
        except Exception as err:
            raise DecryptError("Failed to decrypt.") from err

    def _get_nonce(self, msg: bytes, nonce: bytes = b"") -> bytes:

        if nonce:
            if len(nonce) != 32:
                raise ValueError("nonce must be 32 bytes long.")
        else:
            nonce = token_bytes(32)

        try:
            return hmac.new(nonce, msg, hashlib.sha384).digest()[0:32]
        except Exception as err:
            raise EncryptError("Failed to get nonce.") from err


class V1Public(KeyInterface):
    """
    The key object for v1.public.
    """

    def __init__(self, key: Union[str, bytes]):

        super().__init__(1, "public", key)
        self._sig_size = 256

        if not isinstance(self._key, (RSAPublicKey, RSAPrivateKey)):
            raise ValueError("The key is not RSA key.")

        self._padding = padding.PSS(mgf=padding.MGF1(hashes.SHA384()), salt_length=48)
        return

    def sign(
        self, payload: bytes, footer: bytes = b"", implicit_assertion: bytes = b""
    ) -> bytes:

        if isinstance(self._key, RSAPublicKey):
            raise ValueError("A public key cannot be used for signing.")
        m2 = pae([self.header, payload, footer])
        try:
            return self._key.sign(m2, self._padding, hashes.SHA384())
        except Exception as err:
            raise SignError("Failed to sign.") from err

    def verify(
        self, payload: bytes, footer: bytes = b"", implicit_assertion: bytes = b""
    ):

        if len(payload) <= self._sig_size:
            raise ValueError("Invalid payload.")

        sig = payload[-self._sig_size :]
        m = payload[: len(payload) - self._sig_size]
        k = self._key if isinstance(self._key, RSAPublicKey) else self._key.public_key()
        m2 = pae([self.header, m, footer])
        try:
            k.verify(sig, m2, self._padding, hashes.SHA384())
        except Exception as err:
            raise VerifyError("Failed to verify.") from err
        return m

    def to_paserk(self, seed: bytes = b"") -> str:
        if isinstance(self._key, RSAPublicKey):
            return "k1.public." + base64url_encode(
                self._key.public_bytes(
                    encoding=serialization.Encoding.DER,
                    format=serialization.PublicFormat.SubjectPublicKeyInfo,
                )
            ).decode("utf-8")
        return "k1.secret." + base64url_encode(
            seed
            + self._key.private_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        ).decode("utf-8")
