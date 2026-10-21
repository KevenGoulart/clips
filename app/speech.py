"""Texto falável (pt-BR): o Chatterbox não sabe ler algarismos, símbolos e siglas, então engasga ou soletra.
Antes de narrar, números, anos, porcentagens, moedas, séculos em romano e abreviações viram palavras.
As legendas continuam mostrando o texto original."""
import re

_U = ("zero um dois três quatro cinco seis sete oito nove dez onze doze treze catorze quinze "
      "dezesseis dezessete dezoito dezenove").split()
_T = "_ _ vinte trinta quarenta cinquenta sessenta setenta oitenta noventa".split()
_H = "_ cento duzentos trezentos quatrocentos quinhentos seiscentos setecentos oitocentos novecentos".split()
_SCALES = [("", ""), ("mil", "mil"), ("milhão", "milhões"), ("bilhão", "bilhões"), ("trilhão", "trilhões")]
_ORD_U = "_ primeiro segundo terceiro quarto quinto sexto sétimo oitavo nono".split()
_ORD_T = ("_ décimo vigésimo trigésimo quadragésimo quinquagésimo sexagésimo septuagésimo octogésimo "
          "nonagésimo").split()
_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def _sub1000(n: int) -> str:
    if n < 20:
        return _U[n]
    if n < 100:
        return _T[n // 10] + (" e " + _U[n % 10] if n % 10 else "")
    if n == 100:
        return "cem"
    return _H[n // 100] + (" e " + _sub1000(n % 100) if n % 100 else "")


def cardinal(n: int) -> str:
    if n < 1000:
        return _sub1000(n)
    groups = []
    while n:
        groups.append(n % 1000)
        n //= 1000
    if len(groups) > len(_SCALES):
        return " ".join(_U[int(d)] for d in str(n))
    parts = []  # (valor do grupo, texto), do maior para o menor
    for i in range(len(groups) - 1, -1, -1):
        g = groups[i]
        if not g:
            continue
        if i == 0:
            txt = _sub1000(g)
        elif i == 1:
            txt = "mil" if g == 1 else _sub1000(g) + " mil"
        else:
            txt = _sub1000(g) + " " + _SCALES[i][0 if g == 1 else 1]
        parts.append((g, txt))
    out = parts[0][1]
    for k, (g, txt) in enumerate(parts[1:], 1):
        last = k == len(parts) - 1
        # "mil e quinhentos", "dois mil e dez", mas "mil seiscentos e três"
        out += (" e " if last and (g < 100 or g % 100 == 0) else " ") + txt
    return out


def ordinal(n: int, fem=False) -> str:
    if n == 100:
        txt = "centésimo"
    elif 0 < n < 100:
        txt = " ".join(w for w in (_ORD_T[n // 10] if n >= 10 else "", _ORD_U[n % 10] if n % 10 else "") if w)
    else:
        return cardinal(n)
    return re.sub(r"o\b", "a", txt) if fem else txt


def _number(s: str) -> str:
    """'1.500' -> mil e quinhentos; '3,5' -> três vírgula cinco; '1603' -> mil seiscentos e três."""
    whole, _, frac = s.replace(".", "").partition(",")
    out = cardinal(int(whole))
    if frac:
        out += " vírgula " + (cardinal(int(frac)) if not frac.startswith("0") or len(frac) == 1
                              else " ".join(_U[int(d)] for d in frac))
    return out


def _roman(s: str) -> int | None:
    vals = [_ROMAN[c] for c in s]
    n = sum(-v if i + 1 < len(vals) and v < vals[i + 1] else v for i, v in enumerate(vals))
    return n if 0 < n < 4000 and re.fullmatch(r"M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})", s) else None


_MASC_AS = set("dias problemas sistemas programas mapas planetas idiomas temas climas poemas cometas "
               "pijamas dramas telegramas quilogramas gramas atletas".split())
_FEM = set("vezes cidades mulheres gerações nações regiões populações unidades espécies vítimas pessoas "
           "toneladas línguas leis mortes ordens viagens imagens tribos famílias mães ilhas "
           "vez cidade mulher geração nação região pessoa tonelada língua lei morte hora semana".split())


def _is_feminine(word: str | None) -> bool:
    """Heurística pelo substantivo depois do número: 'trezentas pessoas', 'duas vezes', mas 'dois dias'."""
    if not word or word in _MASC_AS:
        return False
    return word in _FEM or word.endswith("as")


def _feminine(txt: str) -> str:
    # só abaixo de "milhão" (que é masculino): "dois milhões de pessoas", mas "duzentas mil pessoas"
    if re.search(r"milh|bilh|trilh", txt):
        return txt
    return re.sub(r"entos\b", "entas", re.sub(r"\bdois\b", "duas", re.sub(r"\bum\b", "uma", txt)))


NUM = r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?"
SCALE = r"mil|milhão|milhões|bilhão|bilhões|trilhão|trilhões"
CURRENCY = {"R$": ("real", "reais"), "US$": ("dólar", "dólares"), "$": ("dólar", "dólares"),
            "€": ("euro", "euros"), "£": ("libra", "libras"), "¥": ("iene", "ienes")}
_LETTERS = dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", ("á bê cê dê é efe gê agá i jota cá ele eme ene ó pê quê erre esse tê u "
                                                   "vê dáblio xis ípsilon zê").split()))


def _spell(s: str) -> str:
    return " ".join(_LETTERS[c] for c in s)


def _acronym(m) -> str:
    """Sigla que não dá para ler como palavra é soletrada: ATSB, FBI, MYT; NASA e CIA continuam lidas."""
    s = m[0]
    readable = re.search(r"[AEIOU]", s) and not re.match(r"[^AEIOU]{2}", s) and not re.search(r"[^AEIOU]{3}", s)
    return s if readable or (set(s) <= set(_ROMAN) and _roman(s)) else _spell(s)


ABBREV = [  # (padrão, troca), aplicados em ordem
    (r"\ba\.\s?C\.", "antes de Cristo"), (r"\bd\.\s?C\.", "depois de Cristo"),
    (r"\bkm²", "quilômetros quadrados"), (r"\bkm/h\b", "quilômetros por hora"), (r"\bkm\b", "quilômetros"),
    (r"\bkg\b", "quilos"), (r"\bm²", "metros quadrados"), (r"°C\b", " graus Celsius"), (r"°", " graus"),
    (r"\bn[º°]\s?", "número "), (r"\bSr\.", "senhor"), (r"\bSra\.", "senhora"), (r"\bDr\.", "doutor"),
    (r"\bDra\.", "doutora"), (r"\betc\.", "etcétera"), (r"\bEUA\b", "Estados Unidos"),
    (r"\bONU\b", "Onu"), (r"\bOTAN\b", "Otan"), (r"\bURSS\b", "União Soviética"), (r"&", " e "),
]


def speakable(text: str) -> str:
    t = text
    for pat, rep in ABBREV:
        t = re.sub(pat, rep, t)
    # moedas: "R$ 2,5 bilhões" -> dois vírgula cinco bilhões de reais
    def money(m):
        sing, plur = CURRENCY[m[1]]
        n, scale = m[2], m[3]
        if scale:
            return f"{_number(n)} {scale}" + (" de " if scale != "mil" else " ") + plur
        return f"{_number(n)} " + (sing if n == "1" else plur)
    t = re.sub(rf"(R\$|US\$|\$|€|£|¥)\s?({NUM})(?:\s({SCALE})\b)?", money, t)
    # intervalos de anos: 1939–1945 -> de ... a ...
    t = re.sub(r"\b(\d{3,4})\s?[–-]\s?(\d{2,4})\b", r"\1 a \2", t)
    t = re.sub(rf"({NUM})\s?%", lambda m: _number(m[1]) + " por cento", t)
    t = re.sub(r"\b(\d{1,3})\s?([ºª])", lambda m: ordinal(int(m[1]), m[2] == "ª"), t)
    t = re.sub(r"\b(\d{1,2})h(\d{2})?\b", lambda m: _feminine(cardinal(int(m[1]))) + (" horas" if m[1] != "1" else " hora")
               + (" e " + cardinal(int(m[2])) if m[2] and m[2] != "00" else ""), t)
    # "1:19" -> uma e dezenove; "13:00" -> treze horas
    t = re.sub(r"\b(\d{1,2}):(\d{2})\b", lambda m: _feminine(cardinal(int(m[1])))
               + (" e " + cardinal(int(m[2])) if m[2] != "00" else (" horas" if m[1] != "1" else " hora")), t)
    # códigos com letras e número: "MH370" -> eme agá trezentos e setenta; "B-52" -> bê cinquenta e dois
    t = re.sub(r"\b([A-Z]{1,4})-?(\d{1,4})\b", lambda m: f"{_spell(m[1])} {cardinal(int(m[2]))}", t)
    t = re.sub(rf"\b({NUM})\b(?=\s+(?:mil\s+)?([a-zà-ÿ]+))?",
               lambda m: _feminine(_number(m[1])) if _is_feminine(m[2]) and "," not in m[1] else _number(m[1]), t)
    # romanos: "século XIX" -> século dezenove; "Pedro II" -> Pedro segundo; "Luís XIV" -> Luís catorze
    t = re.sub(r"\b([Ss]éculos?)\s+([IVXLCDM]+)\b",
               lambda m: f"{m[1]} {cardinal(n)}" if (n := _roman(m[2])) else m[0], t)
    t = re.sub(r"\b([A-ZÀ-Ý][a-zà-ÿ]+)\s+([IVX]{2,5}|I)\b",
               lambda m: f"{m[1]} {ordinal(n) if n <= 10 else cardinal(n)}" if (n := _roman(m[2])) else m[0], t)
    t = re.sub(r"\b[A-Z]{2,5}\b", _acronym, t)
    return re.sub(r"\s{2,}", " ", t).strip()


def words(text: str) -> list[str]:
    """Palavras faladas, normalizadas, para comparar o texto pedido com o que a transcrição ouviu."""
    return re.findall(r"\w+", speakable(text).lower())
