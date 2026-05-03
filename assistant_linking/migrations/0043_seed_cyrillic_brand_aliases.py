import re
import unicodedata

from django.db import migrations


BRAND_ALIAS_ROWS_TSV = """
Аберкромби & Фитч	Abercrombie & Fitch
Аджмал	Ajmal
Адидас	Adidas
Аззаро	Azzaro
Айзенберг	Eisenberg
Айсберг	Iceberg
Аква ди Парма	Acqua di Parma
Акро	Akro
Александр J.	Alexandre J.
Аль Харамейн	Al Haramain
Альфред Данхилл	Alfred Dunhill
Амуаж	Amouage
Ангел Шлессер	Angel Schlesser
Анник Гуталь	Annick Goutal
АНТ. БАН.	Antonio Banderas
Антонио Моретти	Antonio Maretti
Арамис	Aramis
Арден	Elizabeth Arden
Арманд Баси	Armand Basi
Армани	Armani
Артизан	L'Artisan Parfumeur
Ателье Колонь	Atelier Cologne
Аттар	Attar Collection
Афнан	Afnan
Балдессарини	Baldessarini
Балман	Balmain
Барбери	Burberry
Бентли	Bentley
Блюмарин	Blumarine
Босс	Hugo Boss
Бриони	Brioni
Бритни Спирс	Britney Spears
Брокард	Brocard
Бугатти	Bugatti
Буж	Bouge
Булгари	Bvlgari
Бушерон	Boucheron
Ван Клиф	Van Cleef & Arpels
Ванс	Once
Версаче	Versace
Вертус	Vertus
Виктор Рольф	Viktor & Rolf
Виктория Сикрет	Victoria's Secret
Вуменс Секрет	Women Secret
Габриэла Сабитина	Gabriela Sabatini
Гай Лароше	Guy Laroche
Гепарлис	Geparlys
Герлен	Guerlain
Гермес	Hermes
Гесс	Guess
Гост	Ghost
Готье	Jean Paul Gaultier
Грес	Gres
Грэхэм и Потт	Graham & Pott
Гуччи	Gucci
Давыдофф	Davidoff
Де Лавие Парфюмс	De Lavie Parfums
Дженнифер Лопес	Jennifer Lopez
Джессика Паркер	Sarah Jessica Parker
Джил Сандер	Jil Sander
Джимми Чу	Jimmy Choo
Джо Малон	Jo Malone
Джон Ричмонд	John Richmond
Джульетта	Juliette Has a Gun
Джуси Кутюр	Juicy Couture
Диана Фон Фюрстенберг	Diane von Furstenberg
Дизель	Diesel
Диор	Dior
Диптик	Diptyque
Дискваред2	Dsquared2
Дольче Габбана	Dolce & Gabbana
Донна Каран	Donna Karan DKNY
Дюпон	S.T. Dupont
Жан Пату	Jean Patou
Жан-Луи Шерер	Jean-Louis Scherrer
Живанши	Givenchy
Задиг&Вольтер	Zadig & Voltaire
Заркопарфюм	Zarkoperfume
Ив Сен Лоран	Yves Saint Laurent
Инитио	Initio
ИСА	Isa
Иссей Мияке	Issey Miyake
Йоп	Joop!
Кайли Миноуг	Kylie Minogue
Карл Лагерфельд	Karl Lagerfeld
Каролина Эррера	Carolina Herrera
Картье	Cartier
Кастельбажак	Castelbajac
Кашарель	Cacharel
Кейт Спейд	Kate Spade
Кельвин Кляйн	Calvin Klein
Кензо	Kenzo
Килиан	Kilian
Клайв Кристиан	Clive Christian
Клиник	Clinique
Космогония	Cosmogony
Костюм Националь	Costume National
Коуч	Coach
Крид	Creed
Кристина Агилера	Christina Aguilera
Криштиано Роналдо	Cristiano Ronaldo
Культ	Cult
Кураж	Courreges
Ла Перла	La Perla
Лав Ти Арт	Love Tea Art
Лазур Парфюм	Lazure Perfumes
Лакосте	Lacoste
Лалик	Lalique
Ланвин	Lanvin
Ланком	Lancome
Лаура Биаджотти	Laura Biagiotti
Лаура Мерсье	Laura Mercier
Ле Бонер	Le Bonheur
Ле Дестинасьон	Les Destinations
Ле Лабо	Le Labo
Лоеве	Loewe
Лолита Лемпика	Lolita Lempicka
ЛПДО	LPDO
Лулу Кастаньет	Lulu Castagnette
Майкл Корс	Michael Kors
Мандарина Дак	Mandarina Duck
Мансера	Mancera
Марина де Бурбон	Marina De Bourbon
Марк Якобс	Marc Jacobs
Мекс	Mexx
Мемо	Memo
Мемори	Memory
Мемуар де Сенс	Memoire Des Sens
Мерседес Бенц	Mercedes-Benz
Микаллеф	M. Micallef
Милл Кентум	Mille Centum
Мин Нью-Йорк	Min New York
Минт	M.INT
Миссони	Missoni
Молекула	Escentric Molecules
Монблан	Montblanc
Монклер	Moncler
Монталь	Montale
Москино	Moschino
Муд	Mood
Нарцисо Родригес	Narciso Rodriguez
Насо ди Раца	Naso Di Raza
Наутика	Nautica
Никос	Nikos
Нина Риччи	Nina Ricci
Норана Парфюм	Norana Perfumes
Орлов	Orlov
Ормонде Джейн	Ormonde Jayne
Оскар де ла Рента	Oscar de la Renta
Пако Рабан	Paco Rabanne
Палома Пикассо	Paloma Picasso
Парфюм де Марли	Parfums de Marly
Паскаль Морабито	Pascal Morabito
Пенхалигонс	Penhaligon's
Пеп Джинс	Pepe Jeans
Пол Смит	Paul Smith
Порше	Porsche
Прада	Prada
Ральф Лорен	Ralph Lauren
Рансе 1795	Rance 1795
РАСАСИ	Rasasi
Реми Латур	Remy Latour
Роберт Пиге	Robert Piguet
Роберто Кавали	Roberto Cavalli
Рокко Барокко	Roccobarocco
Роша	Rochas
Рубеус Милано	Rubeus Milano
Руж Банни	Rouge Bunny Rouge
Рус энд Рус	Roos & Roos
Сааб	Elie Saab
Сальвадор Дали	Salvador Dali
Свисс Арабиан	Swiss Arabian
Серджио Тачини	Sergio Tacchini
Серж Лютен	Serge Lutens
Сигнатюр	Signature
Сислей	Sisley
Стефан Гумберт Лукас	Stephane Humbert Lucas 777
Тамин	Thameen
Тед Лапидус	Ted Lapidus
Теодор Калотинис	Theodoros Kalotinis
Терри Мюглер	Thierry Mugler
Тиффани энд Ко	Tiffany & Co
Тициана Терензи	Tiziana Terenzi
Том Форд	Tom Ford
Томас Космала	Thomas Kosmala
Три оф лайф	Tree Of Life
Труссарди	Trussardi
Унгаро	Emanuel Ungaro
Феррагамо	Salvatore Ferragamo
Ферре	Gianfranco Ferre
Филипп Плейн	Philipp Plein
Формула F1	F1 Parfums
Франк Букле	Franck Boclet
Франк Оливер	Franck Olivier
Франческа Бьянки	Francesca Bianchi
Фредерик Майл	Frederic Malle
Фурла	Furla
Хакет	Hackett London
Хаяри	Hayari Parfums
Хлоя	Chloe
Хот Фрагранс	HFC
Черутти  1881	Cerruti 1881
Шанель	Chanel
Шисейдо	Shiseido
Шопард	Chopard
Экс Нихило	Ex Nihilo
Эмилио Пуччи	Emilio
Эскада	Escada
Эсте Лаудер	Estee Lauder
Ю	You
Ямамото	Yohji Yamamoto
Яхт Мен	Myrurgia Yacht Man
"""


CANONICAL_BRAND_FALLBACKS = {
    "Annick Goutal": "Annick Gooutal",
    "Donna Karan DKNY": "Donna Karan",
    "Orlov": "Orlov Paris",
    "Porsche": "Porsche Design",
    "Emilio": "Emilio Pucci",
    "Myrurgia Yacht Man": "Myrurgia",
}


def normalize_alias(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").lower()
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)
    text = re.sub(r"(?<!\d)\.(?!\d)", " ", text)
    text = re.sub(r"[\u00a0_&/,;:|()\[\]{}]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def brand_alias_rows() -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    rows: list[tuple[str, str]] = []
    for line in BRAND_ALIAS_ROWS_TSV.splitlines():
        line = line.strip()
        if not line or "\t" not in line:
            continue
        alias_text, brand_name = [part.strip() for part in line.split("\t", 1)]
        key = (alias_text, brand_name)
        if key not in seen:
            seen.add(key)
            rows.append(key)
    return rows


def resolve_brand(Brand, brand_name: str):
    brand = Brand.objects.filter(name__iexact=brand_name).first()
    if brand:
        return brand
    fallback = CANONICAL_BRAND_FALLBACKS.get(brand_name)
    if fallback:
        return Brand.objects.filter(name__iexact=fallback).first()
    return None


def seed_cyrillic_brand_aliases(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    BrandAlias = apps.get_model("assistant_linking", "BrandAlias")
    for alias_text, brand_name in brand_alias_rows():
        normalized_alias = normalize_alias(alias_text)
        if len(normalized_alias) < 2:
            continue
        brand = resolve_brand(Brand, brand_name)
        if not brand:
            continue
        BrandAlias.objects.update_or_create(
            alias_text=alias_text,
            supplier=None,
            brand=brand,
            defaults={
                "normalized_alias": normalized_alias,
                "active": True,
                "priority": 35,
                "is_regex": False,
            },
        )


def unseed_cyrillic_brand_aliases(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    BrandAlias = apps.get_model("assistant_linking", "BrandAlias")
    for alias_text, brand_name in brand_alias_rows():
        brand = resolve_brand(Brand, brand_name)
        if brand:
            BrandAlias.objects.filter(
                alias_text=alias_text,
                supplier=None,
                brand=brand,
            ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_linking", "0042_seed_brand_alias_safety_rules"),
    ]

    operations = [
        migrations.RunPython(seed_cyrillic_brand_aliases, unseed_cyrillic_brand_aliases),
    ]
