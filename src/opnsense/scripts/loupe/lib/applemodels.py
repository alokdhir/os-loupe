# Copyright (C) 2026 Alok K. Dhir
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
# INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
# AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
# OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

"""Apple model identifiers (as announced over mDNS/AirPlay) -> marketing names.

Best effort; unknown identifiers fall back to the device family.
"""
MODELS = {
    # Macs (Apple silicon)
    "MacBookAir10,1": "MacBook Air 13″ (M1)",
    "MacBookPro17,1": "MacBook Pro 13″ (M1)",
    "MacBookPro18,1": "MacBook Pro 16″ (M1 Pro)", "MacBookPro18,2": "MacBook Pro 16″ (M1 Max)",
    "MacBookPro18,3": "MacBook Pro 14″ (M1 Pro)", "MacBookPro18,4": "MacBook Pro 14″ (M1 Max)",
    "Macmini9,1": "Mac mini (M1)",
    "iMac21,1": "iMac 24″ (M1)", "iMac21,2": "iMac 24″ (M1)",
    "Mac13,1": "Mac Studio (M1 Max)", "Mac13,2": "Mac Studio (M1 Ultra)",
    "Mac14,2": "MacBook Air 13″ (M2)", "Mac14,15": "MacBook Air 15″ (M2)",
    "Mac14,7": "MacBook Pro 13″ (M2)",
    "Mac14,5": "MacBook Pro 14″ (M2 Max)", "Mac14,9": "MacBook Pro 14″ (M2 Pro)",
    "Mac14,6": "MacBook Pro 16″ (M2 Max)", "Mac14,10": "MacBook Pro 16″ (M2 Pro)",
    "Mac14,3": "Mac mini (M2)", "Mac14,12": "Mac mini (M2 Pro)",
    "Mac14,13": "Mac Studio (M2 Max)", "Mac14,14": "Mac Studio (M2 Ultra)", "Mac14,8": "Mac Pro (M2 Ultra)",
    "Mac15,3": "MacBook Pro 14″ (M3)",
    "Mac15,6": "MacBook Pro 14″ (M3 Pro)", "Mac15,8": "MacBook Pro 14″ (M3 Max)", "Mac15,10": "MacBook Pro 14″ (M3 Max)",
    "Mac15,7": "MacBook Pro 16″ (M3 Pro)", "Mac15,9": "MacBook Pro 16″ (M3 Max)", "Mac15,11": "MacBook Pro 16″ (M3 Max)",
    "Mac15,4": "iMac 24″ (M3)", "Mac15,5": "iMac 24″ (M3)",
    "Mac15,12": "MacBook Air 13″ (M3)", "Mac15,13": "MacBook Air 15″ (M3)",
    "Mac15,14": "Mac Studio (M3 Ultra)",
    "Mac16,1": "MacBook Pro 14″ (M4)",
    "Mac16,6": "MacBook Pro 14″ (M4 Max)", "Mac16,8": "MacBook Pro 14″ (M4 Pro)",
    "Mac16,5": "MacBook Pro 16″ (M4 Max)", "Mac16,7": "MacBook Pro 16″ (M4 Pro)",
    "Mac16,2": "iMac 24″ (M4)", "Mac16,3": "iMac 24″ (M4)",
    "Mac16,10": "Mac mini (M4)", "Mac16,11": "Mac mini (M4 Pro)",
    "Mac16,12": "MacBook Air 13″ (M4)", "Mac16,13": "MacBook Air 15″ (M4)",
    "Mac16,9": "Mac Studio (M4 Max)",
    # Apple TV
    "AppleTV5,3": "Apple TV HD", "AppleTV6,2": "Apple TV 4K (1st gen)",
    "AppleTV11,1": "Apple TV 4K (2nd gen)", "AppleTV14,1": "Apple TV 4K (3rd gen)",
    # HomePod
    "AudioAccessory1,1": "HomePod", "AudioAccessory1,2": "HomePod",
    "AudioAccessory5,1": "HomePod mini", "AudioAccessory6,1": "HomePod (2nd gen)",
}


def name(identifier):
    return MODELS.get(identifier)
