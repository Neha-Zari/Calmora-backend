try:
    from py_vapid import Vapid01
except ImportError:
    print('py-vapid is installed as a dependency of pywebpush.')
    raise
v = Vapid01()
v.generate_keys()
print('VAPID_PUBLIC_KEY=' + v.public_key.decode() if isinstance(v.public_key, bytes) else 'VAPID_PUBLIC_KEY=' + str(v.public_key))
print('VAPID_PRIVATE_KEY=' + v.private_key.decode() if isinstance(v.private_key, bytes) else 'VAPID_PRIVATE_KEY=' + str(v.private_key))
