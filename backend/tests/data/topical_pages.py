"""Eight short pages on two unrelated topics, used to calibrate the internal-link similarity threshold."""

COFFEE = {
    "https://t.example/coffee/pour-over": (
        "Pour over coffee guide",
        "Pour over coffee rewards patience. Rinse the paper filter, add medium ground coffee, and pour hot water in slow circles. "
        "The bloom, a short pause after the first pour, lets trapped gas escape so the water can extract sweetness evenly. "
        "A gooseneck kettle gives control over the stream, and a scale keeps the brew ratio steady at sixteen parts water to one part coffee.",
    ),
    "https://t.example/coffee/espresso": (
        "Espresso basics for home baristas",
        "Espresso needs fine ground coffee, firm tamping and about nine bars of pressure. A shot should run for roughly thirty seconds "
        "and produce a syrupy crema. If the shot runs fast, grind finer; if it chokes the machine, grind coarser. "
        "Fresh roasted beans, a clean portafilter and a good grinder matter more than an expensive espresso machine.",
    ),
    "https://t.example/coffee/grinders": (
        "Choosing a coffee grinder",
        "A burr grinder produces even particles, which means even extraction and a cleaner cup of coffee. Blade grinders chop beans "
        "unevenly and leave both dust and boulders. For pour over and espresso, a conical or flat burr grinder with stepless adjustment "
        "lets you dial in the grind size precisely for every brew method.",
    ),
    "https://t.example/coffee/beans": (
        "How to store coffee beans",
        "Roasted coffee beans go stale as oxygen, light and moisture reach them. Keep beans in an airtight container in a cool cupboard, "
        "never the refrigerator, and grind only what you need for each brew. Beans taste best within a few weeks of the roast date, "
        "and a one way valve bag keeps freshly roasted coffee lively for longer.",
    ),
}
HOSTING = {
    "https://t.example/hosting/vps": (
        "VPS hosting explained",
        "A virtual private server gives you dedicated memory and CPU slices on shared hardware. You get root access to install software, "
        "configure the firewall and run several websites. Compared with shared hosting, a VPS handles traffic spikes better, but you "
        "manage updates, backups and security patches for the operating system yourself.",
    ),
    "https://t.example/hosting/dns": (
        "How DNS records work",
        "The domain name system translates a domain into an IP address. An A record points a name at an IPv4 address, a CNAME aliases "
        "one name to another, and MX records route email. DNS changes can take hours to propagate because resolvers cache answers "
        "according to the time to live value set on each record.",
    ),
    "https://t.example/hosting/ssl": (
        "SSL certificates and HTTPS",
        "An SSL certificate encrypts traffic between the browser and your web server and proves the domain is yours. Free certificates "
        "from Let's Encrypt renew automatically every ninety days. After installing a certificate, redirect all http requests to https "
        "and update internal links, otherwise browsers may warn about mixed content.",
    ),
    "https://t.example/hosting/backups": (
        "Website backup strategy",
        "Back up your website files and database on a schedule, and store copies away from the server. Test restores regularly, because "
        "a backup you cannot restore is worthless. Managed hosting often includes daily snapshots, while a VPS needs cron jobs, "
        "a retention policy and offsite storage for disaster recovery.",
    ),
}
PAGES = {**COFFEE, **HOSTING}
