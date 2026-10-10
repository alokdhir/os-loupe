"""Friendly service names: domain suffix -> service, plus address-range and port fallbacks.

Applied when reports are built (not stored), so edits to the map apply to all history.
Users add or override suffixes in the settings; those win over the built-in table.
"""
import ipaddress

SUFFIXES = {
    # video
    "youtube.com": "YouTube", "googlevideo.com": "YouTube", "ytimg.com": "YouTube", "youtubei.googleapis.com": "YouTube",
    "youtube-nocookie.com": "YouTube", "yt3.ggpht.com": "YouTube",
    "netflix.com": "Netflix", "nflxvideo.net": "Netflix", "nflximg.net": "Netflix", "nflxext.com": "Netflix",
    "nflxso.net": "Netflix",
    "disneyplus.com": "Disney+", "dssott.com": "Disney+", "bamgrid.com": "Disney+", "disney-plus.net": "Disney+",
    "hulu.com": "Hulu", "hulustream.com": "Hulu", "huluim.com": "Hulu",
    "max.com": "Max", "hbomax.com": "Max", "hbo.com": "Max",
    "primevideo.com": "Prime Video", "aiv-cdn.net": "Prime Video", "aiv-delivery.net": "Prime Video",
    "pv-cdn.net": "Prime Video", "media-amazon.com": "Amazon",
    "tv.apple.com": "Apple TV+", "hls.itunes.apple.com": "Apple TV+", "play-edge.itunes.apple.com": "Apple TV+",
    "peacocktv.com": "Peacock", "paramountplus.com": "Paramount+", "cbsivideo.com": "Paramount+",
    "plex.tv": "Plex", "plex.direct": "Plex", "plex.bz": "Plex",
    "twitch.tv": "Twitch", "ttvnw.net": "Twitch", "jtvnw.net": "Twitch",
    "vimeo.com": "Vimeo", "vimeocdn.com": "Vimeo",
    "roku.com": "Roku", "sling.com": "Sling TV", "fubo.tv": "Fubo", "youtube.googleapis.com": "YouTube",
    "channelsdvr.net": "Channels DVR", "pluto.tv": "Pluto TV", "tubi.io": "Tubi", "tubitv.com": "Tubi",
    # music / audio
    "spotify.com": "Spotify", "scdn.co": "Spotify", "spotifycdn.com": "Spotify", "spotilocal.com": "Spotify",
    "pscdn.co": "Spotify",
    "music.apple.com": "Apple Music", "mzstatic.com": "Apple", "pandora.com": "Pandora", "sonos.com": "Sonos",
    "audible.com": "Audible", "siriusxm.com": "SiriusXM", "soundcloud.com": "SoundCloud",
    # social
    "facebook.com": "Facebook", "fbcdn.net": "Facebook", "facebook.net": "Facebook", "fb.com": "Facebook",
    "instagram.com": "Instagram", "cdninstagram.com": "Instagram",
    "whatsapp.net": "WhatsApp", "whatsapp.com": "WhatsApp",
    "tiktok.com": "TikTok", "tiktokcdn.com": "TikTok", "tiktokv.com": "TikTok", "byteoversea.com": "TikTok",
    "ibytedtos.com": "TikTok", "tiktokcdn-us.com": "TikTok", "ttwstatic.com": "TikTok",
    "snapchat.com": "Snapchat", "sc-cdn.net": "Snapchat", "snapkit.com": "Snapchat", "sc-static.net": "Snapchat",
    "twitter.com": "X", "x.com": "X", "twimg.com": "X", "t.co": "X",
    "reddit.com": "Reddit", "redd.it": "Reddit", "redditmedia.com": "Reddit", "redditstatic.com": "Reddit",
    "linkedin.com": "LinkedIn", "licdn.com": "LinkedIn", "pinterest.com": "Pinterest", "pinimg.com": "Pinterest",
    "discord.com": "Discord", "discord.gg": "Discord", "discordapp.com": "Discord", "discordapp.net": "Discord",
    "discord.media": "Discord",
    "telegram.org": "Telegram", "signal.org": "Signal", "threads.net": "Threads", "bsky.app": "Bluesky",
    # games
    "playstation.net": "PlayStation Network", "playstation.com": "PlayStation Network",
    "sonyentertainmentnetwork.com": "PlayStation Network",
    "xboxlive.com": "Xbox Live", "xbox.com": "Xbox Live", "gamepass.com": "Xbox Live",
    "nintendo.net": "Nintendo", "nintendo.com": "Nintendo",
    "steampowered.com": "Steam", "steamcommunity.com": "Steam", "steamserver.net": "Steam",
    "steamcontent.com": "Steam", "steamstatic.com": "Steam",
    "epicgames.com": "Epic Games", "epicgames.dev": "Epic Games", "unrealengine.com": "Epic Games",
    "fortnite.com": "Fortnite",
    "roblox.com": "Roblox", "rbxcdn.com": "Roblox", "rbx.com": "Roblox",
    "minecraft.net": "Minecraft", "mojang.com": "Minecraft",
    "ea.com": "EA", "origin.com": "EA", "riotgames.com": "Riot Games", "leagueoflegends.com": "Riot Games",
    "blizzard.com": "Battle.net", "battle.net": "Battle.net", "activision.com": "Activision",
    "callofduty.com": "Activision",
    # AI / dev
    "claude.ai": "Claude", "anthropic.com": "Claude", "openai.com": "ChatGPT", "chatgpt.com": "ChatGPT",
    "oaiusercontent.com": "ChatGPT", "gemini.google.com": "Gemini", "kiro.dev": "Kiro",
    "github.com": "GitHub", "githubusercontent.com": "GitHub", "githubassets.com": "GitHub",
    "cursor.sh": "Cursor", "cursor.com": "Cursor", "copilot.microsoft.com": "Copilot",
    "stackoverflow.com": "Stack Overflow", "npmjs.org": "npm", "pypi.org": "PyPI", "docker.io": "Docker Hub",
    "docker.com": "Docker Hub",
    # work / productivity
    "zoom.us": "Zoom", "zoom.com": "Zoom", "teams.microsoft.com": "Microsoft Teams", "skype.com": "Microsoft Teams",
    "slack.com": "Slack", "slack-edge.com": "Slack", "slack-msgs.com": "Slack",
    "office.com": "Microsoft 365", "office.net": "Microsoft 365", "office365.com": "Microsoft 365",
    "outlook.com": "Microsoft 365", "sharepoint.com": "Microsoft 365", "live.com": "Microsoft",
    "microsoft.com": "Microsoft", "windowsupdate.com": "Windows Update", "msftconnecttest.com": "Microsoft",
    "msedge.net": "Microsoft", "bing.com": "Bing", "azure.com": "Microsoft Azure", "azureedge.net": "Microsoft Azure",
    "dropbox.com": "Dropbox", "dropboxusercontent.com": "Dropbox", "box.com": "Box",
    "notion.so": "Notion", "figma.com": "Figma", "atlassian.net": "Atlassian", "atlassian.com": "Atlassian",
    "webex.com": "Webex", "gotomeeting.com": "GoTo",
    # google
    "google.com": "Google", "googleapis.com": "Google", "gstatic.com": "Google", "googleusercontent.com": "Google",
    "gmail.com": "Gmail", "mail.google.com": "Gmail", "drive.google.com": "Google Drive",
    "docs.google.com": "Google Docs", "meet.google.com": "Google Meet", "doubleclick.net": "Google Ads",
    "googlesyndication.com": "Google Ads", "googleadservices.com": "Google Ads", "google-analytics.com": "Google",
    "app-measurement.com": "Google", "crashlytics.com": "Google", "firebaseio.com": "Google Firebase",
    "nest.com": "Google Nest",
    # apple
    "apple.com": "Apple", "icloud.com": "iCloud", "icloud-content.com": "iCloud", "apple-dns.net": "Apple",
    "aaplimg.com": "Apple", "cdn-apple.com": "Apple", "apple-cloudkit.com": "iCloud", "me.com": "iCloud",
    "push.apple.com": "Apple Push", "courier.push.apple.com": "Apple Push", "mask.icloud.com": "iCloud Private Relay",
    "mask-h2.icloud.com": "iCloud Private Relay", "mask-api.icloud.com": "iCloud Private Relay",
    "swcdn.apple.com": "Apple Software Update", "mesu.apple.com": "Apple Software Update",
    "updates.cdn-apple.com": "Apple Software Update", "gdmf.apple.com": "Apple Software Update",
    "itunes.apple.com": "App Store", "apps.apple.com": "App Store",
    "facetime.apple.com": "FaceTime", "ess.apple.com": "iMessage/FaceTime",
    # amazon
    "amazon.com": "Amazon", "amazonaws.com": "AWS", "cloudfront.net": "CloudFront", "a2z.com": "Amazon",
    "amazon-dss.com": "Alexa", "alexa.amazon.com": "Alexa", "amazonvideo.com": "Prime Video", "ring.com": "Ring",
    # smart home
    "tuyaus.com": "Tuya", "tuya.com": "Tuya", "tuyacn.com": "Tuya", "ecobee.com": "ecobee",
    "meethue.com": "Philips Hue", "lutron.com": "Lutron", "iaqualink.net": "iAquaLink", "myq-cloud.com": "myQ",
    "smartthings.com": "SmartThings", "tplinkcloud.com": "TP-Link Kasa", "tplinknbu.com": "TP-Link Kasa",
    "kasasmart.com": "TP-Link Kasa", "reolink.com": "Reolink", "wyze.com": "Wyze", "wyzecam.com": "Wyze",
    "arlo.com": "Arlo", "eero.com": "eero", "e2ro.com": "eero", "aqara.com": "Aqara", "aqara.cn": "Aqara",
    "fanimation.com": "Fanimation", "fansync.com": "fanSync", "home-assistant.io": "Home Assistant",
    "nabucasa.com": "Home Assistant Cloud", "homebridge.io": "Homebridge", "scrypted.app": "Scrypted",
    "trycloudflare.com": "Cloudflare Tunnel", "cloudflare.com": "Cloudflare", "cloudflare-dns.com": "Cloudflare DNS",
    "one.one.one.one": "Cloudflare DNS", "dns.google": "Google DNS",
    # shopping / news / misc
    "ebay.com": "eBay", "walmart.com": "Walmart", "target.com": "Target", "etsy.com": "Etsy",
    "nytimes.com": "New York Times", "cnn.com": "CNN", "espn.com": "ESPN", "wikipedia.org": "Wikipedia",
    "openweathermap.org": "OpenWeather", "weather.com": "Weather.com",
    "mozilla.org": "Firefox", "mozilla.com": "Firefox", "mozilla.net": "Firefox", "firefox.com": "Firefox",
    "easports.com": "EA", "tnt-ea.com": "EA", "eeroup.com": "eero", "miele.com": "Miele",
    "firetvcaptiveportal.com": "Fire TV", "amazonalexa.com": "Alexa",
    "newrelic.com": "Telemetry", "nr-data.net": "Telemetry", "sentry.io": "Telemetry", "datadoghq.com": "Telemetry",
    "segment.io": "Telemetry", "branch.io": "Telemetry", "appsflyer.com": "Telemetry", "adjust.com": "Telemetry",
    # CDNs / infrastructure (low priority: only when nothing more specific matches)
    "akamaized.net": "Akamai CDN", "akamaihd.net": "Akamai CDN", "akamai.net": "Akamai CDN",
    "edgekey.net": "Akamai CDN", "edgesuite.net": "Akamai CDN", "fastly.net": "Fastly CDN",
    "fastly-edge.com": "Fastly CDN",
    "fastlylb.net": "Fastly CDN", "llnwd.net": "Edgio CDN", "edgecastcdn.net": "Edgio CDN",
    "cloudflare.net": "Cloudflare", "azurefd.net": "Microsoft Azure", "trafficmanager.net": "Microsoft Azure",
    "ntp.org": "NTP", "time.apple.com": "NTP", "time.google.com": "NTP",
    "verizon.net": "Verizon", "verizon.com": "Verizon",
}

# address ranges -> owner, for traffic with no name at all
RANGES = [
    ("17.0.0.0/8", "Apple"),
    ("2620:149::/32", "Apple"), ("2a01:b740::/32", "Apple"),
    ("142.250.0.0/15", "Google"), ("172.217.0.0/16", "Google"), ("216.58.192.0/19", "Google"),
    ("2607:f8b0::/32", "Google"),
    ("31.13.24.0/21", "Facebook"), ("157.240.0.0/16", "Facebook"), ("2a03:2880::/32", "Facebook"),
    ("104.16.0.0/13", "Cloudflare"), ("172.64.0.0/13", "Cloudflare"), ("162.158.0.0/15", "Cloudflare"),
    ("198.41.128.0/17", "Cloudflare"), ("2606:4700::/32", "Cloudflare"),
    ("23.246.0.0/18", "Netflix"), ("45.57.0.0/17", "Netflix"), ("198.38.96.0/19", "Netflix"),
    ("2a00:86c0::/32", "Netflix"),
]

# (proto, remote port) -> service, last resort
PORTS = {
    ("udp", 51820): "WireGuard", ("udp", 1194): "OpenVPN", ("tcp", 1194): "OpenVPN",
    ("udp", 500): "IPsec VPN", ("udp", 4500): "IPsec VPN",
    ("tcp", 22): "SSH", ("udp", 123): "NTP", ("udp", 53): "DNS (outside router)", ("tcp", 53): "DNS (outside router)",
    ("tcp", 853): "DNS over TLS", ("udp", 853): "DNS over QUIC",
    ("tcp", 5223): "Apple Push", ("tcp", 993): "Email (IMAP)", ("tcp", 587): "Email (SMTP)",
    ("tcp", 465): "Email (SMTP)", ("tcp", 8883): "MQTT (IoT cloud)", ("tcp", 1883): "MQTT (IoT cloud)",
    ("udp", 3478): "STUN / calls", ("udp", 3479): "STUN / calls", ("udp", 3480): "STUN / calls",
    ("udp", 3481): "STUN / calls", ("udp", 19302): "Google Meet / WebRTC",
    ("udp", 3074): "Xbox Live", ("udp", 3658): "PlayStation Network", ("tcp", 25565): "Minecraft",
    ("tcp", 7844): "Cloudflare Tunnel", ("udp", 7844): "Cloudflare Tunnel", ("tcp", 32400): "Plex",
}


class ServiceMap:
    def __init__(self, extra=None):
        self.suffixes = dict(SUFFIXES)
        self.suffixes.update({k.lower().strip("."): v for k, v in (extra or {}).items()})
        self.ranges = [(ipaddress.ip_network(n), s) for n, s in RANGES]
        self._cache = {}

    def by_name(self, name):
        if not name:
            return None
        hit = self._cache.get(name)
        if hit is None:
            hit = ""
            parts = name.lower().rstrip(".").split(".")
            for i in range(len(parts)):          # longest suffix first
                s = self.suffixes.get(".".join(parts[i:]))
                if s:
                    hit = s
                    break
            self._cache[name] = hit
        return hit or None

    def by_address(self, addr):
        try:
            a = ipaddress.ip_address(addr)
        except ValueError:
            return None
        for net, s in self.ranges:
            if a.version == net.version and a in net:
                return s
        return None

    def service(self, name, server, port, proto):
        s = self.by_name(name)
        if s:
            return s
        owner = self.by_address(server)
        if owner == "Apple" and proto == "udp" and port == 443:
            return "iCloud Private Relay"   # QUIC to Apple without a name we could see
        return owner or PORTS.get((proto, port))
