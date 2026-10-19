"""World regions for ITU country codes, and centres of EiBi target areas."""

_REGIONS = {
    "Europe": "ALB AND AUT AZR BEL BIH BLR BUL CNR CVA CYP CZE D DNK E EST F FIN FRO G GIB GRC "
              "HNG HOL HRV I IRL ISL JMY KAL KOS LIE LTU LUX LVA MCO MDA MDR MKD MLT MNE NOR "
              "POL POR ROU RUS S SMR SRB SUI SVB SVK SVN UKR",
    "Africa": "AFS AGL ALG AOE ASC BDI BEN BFA BOT CAB CAF CEU CME COD COG COM CPV CTI DJI EGY "
              "ERI ETH GAB GHA GMB GNB GNE GUI KEN LBR LBY LSO MAU MDG MEL MLI MOZ MRC MTN MWI "
              "MYT NGR NIG NMB REU ROD RRW SDN SEN SEY SHN SOM SRL SSD STP SWZ TCD TGO TRC TUN "
              "TZA UGA XBY ZMB ZWE",
    "Middle East": "ARM ARS AZE BHR GEO IRN IRQ ISR JOR KWT LBN OMA PSE QAT SYR TUR UAE XGZ XWB YEM",
    "South Asia": "AFG BGD BTN CLN IND MLD NPL PAK BIO",
    "Central Asia": "KAZ KGZ TJK TKM UZB",
    "East Asia": "CHN HKG J KOR KRE MAC MNG TWN IWA PRV",
    "Southeast Asia": "BRU CBG INS LAO MLA MYA PHL SNG THA TLS VTN XSP CHR ICO",
    "Oceania": "AUS CKH FJI FSM GUM HWA HWL JAR JON KIR MDW MHL MRA NCL NFK NIU NRU NZL OCE "
               "PAQ PHX PLM PLW PNG PTC SLM SMA SMO TKL TON TUV VUT WAK WAL",
    "North America": "ALS BER CAN GRL MEX SPM USA",
    "Caribbean": "ABW AIA ATG ATN BAH BES BLM BRB CG7 CUB CUW CYM DMA DOM GLP GRD HTI JMC KNA "
                 "LCA MAF MRT MSR PTR SAP SXM TCA TRD VCT VIR VRG",
    "Latin America": "ARG B BLZ BOL CHL CLM CPT CTR EQA FLK GPG GTM GUF GUY HND JUF NCG PNR PRG "
                     "PRU SLV SUR URG VEN",
    "Antarctic & islands": "AMS ATA BVT CRO EUR HMD JDN KER MRN SGA SOK SSI",
    "Other": "CLA IW UN XUU",
}

REGION_OF = {code: region for region, codes in _REGIONS.items() for code in codes.split()}
REGIONS = list(_REGIONS)


def region_of(itu: str) -> str:
    return REGION_OF.get(itu, "Other")


# Approximate centres (lat, lon) of EiBi target-area codes, used to guess where
# the transmitting antenna is pointed. Compass-point targets are handled separately.
TARGET_CENTRES = {
    "Eu": (50, 10), "WEu": (47, 2), "CEu": (50, 13), "EEu": (52, 30), "NEu": (60, 15),
    "SEu": (41, 15), "SEE": (43, 23), "SWEu": (40, -4), "NWEu": (54, -2),
    "Af": (5, 20), "NAf": (28, 10), "WAf": (10, -5), "CAf": (3, 20), "EAf": (0, 37),
    "SAf": (-25, 25), "NEAf": (12, 40), "SEAf": (-15, 35), "WIO": (-15, 55),
    "ME": (28, 45), "As": (35, 90), "SAs": (22, 78), "In": (22, 78), "SEA": (8, 108),
    "CAs": (42, 65), "FE": (38, 125), "EAs": (35, 120), "Tib": (31, 88), "Sib": (60, 100),
    "NAs": (60, 100), "CIS": (55, 50), "Cau": (42, 45),
    "Am": (15, -80), "NAm": (42, -95), "ENA": (40, -78), "CNA": (40, -95), "WNA": (40, -118),
    "CAm": (14, -87), "Car": (19, -75), "LAm": (-5, -62), "SAm": (-15, -60),
    "Oc": (-25, 145), "Pac": (-10, -160), "NAO": (50, -30), "SAO": (-20, -15),
    "Glo": None,
}
