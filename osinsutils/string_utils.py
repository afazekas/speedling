import string
import random


# consider importing tempest/lib/common/utils/data_utils instead
def rand_password(length=15):
    """Generate a random password

    :param int length: The length of password that you expect to set
                       (If it's smaller than 3, it's same as 3.)
    :return: a random password. The format is
             '<random upper letter>-<random number>-<random special character>
              -<random ascii letters or digit characters or special symbols>'
             (e.g. 'G2*ac8&lKFFgh%2')
    :rtype: string
    """
    upper = random.choice(string.ascii_uppercase)
    ascii_char = string.ascii_letters
    digits = string.digits
    digit = random.choice(string.digits)
    puncs = '~!@#%^&*_=+'
    punc = random.choice(puncs)
    seed = ascii_char + digits + puncs
    pre = upper + digit + punc
    password = pre + ''.join(random.choice(seed) for x in range(length - 3))
    return password
