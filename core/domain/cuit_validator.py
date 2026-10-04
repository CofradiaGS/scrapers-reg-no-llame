# -*- coding: utf-8 -*-
"""
Dominio: Validador y Generador Algorítmico de CUIT/CUIL Argentino.
Implementa el algoritmo matemático estándar Módulo 11 oficial de ANSES/AFIP
y la inferencia heurística de género sobre nombres de personas físicas para Argentina.
Aislado estrictamente de librerías de infraestructura o red.
"""
from typing import List, Tuple, Optional


def calcular_cuil(dni: str, sexo: str = "M") -> str:
    """
    Calcula el CUIL/CUIT oficial argentino de 11 dígitos a partir del DNI y género
    utilizando el algoritmo matemático Módulo 11 oficial de ANSES.
    
    Reglas de Prefijo y Verificador:
    - Femenino: Prefijo base 27
    - Masculino: Prefijo base 20
    - Manejo de colisiones (resto = 1):
      * Si era Masculino (20) -> prefijo se convierte en 23 y verificador es 9.
      * Si era Femenino (27) -> prefijo se convierte en 23 y verificador es 4.
    - Si resto = 0 -> verificador es 0.
    - Si resto > 1 -> verificador es (11 - resto).
    """
    dni_limpio = "".join(filter(str.isdigit, str(dni)))
    if not dni_limpio:
        return ""
    
    # Pad a 8 dígitos según normativa de DNI argentino
    dni_pad = dni_limpio.zfill(8)
    if len(dni_pad) > 8:
        dni_pad = dni_pad[-8:]

    sexo_norm = (sexo or "M").strip().upper()
    es_mujer = any(k in sexo_norm for k in ("F", "FEM", "MUJER"))
    prefijo = "27" if es_mujer else "20"

    base_10 = prefijo + dni_pad
    multiplicadores = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2]
    suma = sum(int(d) * m for d, m in zip(base_10, multiplicadores))
    resto = suma % 11

    if resto == 0:
        verificador = 0
    elif resto == 1:
        prefijo = "23"
        verificador = 4 if es_mujer else 9
    else:
        verificador = 11 - resto

    return f"{prefijo}{dni_pad}{verificador}"


def validar_cuit_modulo11(cuit: str) -> bool:
    """
    Valida si un CUIT/CUIL de 11 dígitos cumple con el algoritmo Módulo 11 oficial de AFIP/ANSES.
    """
    c = "".join(filter(str.isdigit, str(cuit or "")))
    if len(c) != 11:
        return False
    multiplicadores = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2]
    suma = sum(int(c[i]) * multiplicadores[i] for i in range(10))
    resto = suma % 11
    if resto == 0:
        dv = 0
    elif resto == 1:
        dv = 9 if c.startswith("23") else 4
    else:
        dv = 11 - resto
    return dv == int(c[10])



# Conjuntos de conocimiento onomástico argentino
_NOMBRES_FEMENINOS = frozenset({
    "MARIA", "ANA", "PETRONA", "ESTER", "ESTHER", "CRISTINA", "ELISA", "LUISA", "MAGDALENA",
    "ANTONIA", "ZULEMA", "TERESA", "ANGELA", "ROSARIO", "MARTINA", "RUFINA", "ROSITA", "ZOILA",
    "ELSA", "BEATRIZ", "NELLY", "TERESITA", "ESTELA", "MARTA", "MARTHA", "NORMA", "GRACIELA",
    "SUSANA", "LIDIA", "GLADYS", "SILVIA", "MIRTA", "ALICIA", "MONICA", "PATRICIA", "CARMEN",
    "ROSA", "JUANA", "OLGA", "IRMA", "YOLANDA", "DELIA", "NELIDA", "ELENA", "HILDA", "RAMONA",
    "INES", "NOEMI", "CLEMENCIA", "AMALIA", "BLANCA", "HAYDEE", "MERCEDES", "FRANCISCA",
    "ISOLINA", "TRANSITO", "VALLE", "LUCIA", "LAURA", "ANDREA", "PAULA", "VALERIA", "NATALIA",
    "FLORENCIA", "CAMILA", "SOFIA", "JULIETA", "AGUSTINA", "DANIELA", "CAROLINA", "GABRIELA",
    "ROMINA", "LILIANA", "MARIANA", "CECILIA", "VERONICA", "LORENA", "CLAUDIA", "ALEJANDRA",
    "SANDRA", "VIVIANA", "CLARA", "IRENE", "SARA", "EVA", "RITA", "DOLORES", "AURORA",
    "FELISA", "VICTORIA", "FELICITAS", "CLELIA", "OFELIA", "DORIS", "EMMA", "CELIA",
    "CELINA", "JUSTINA", "EUSEBIA", "DIONISIA", "SATURNINA", "GREGORIA", "GUMERSINDA", "CIRILA",
    "BALBINA", "HERMINIA", "DOMINGA", "MODESTA", "LEONOR", "AZUCENA", "VIOLETA", "LILIAN",
    "MIRIAM", "MYRIAM", "CARINA", "KARINA", "VANESA", "VANESSA", "ADRIANA", "MARISA",
    "SILVANA", "ROSANA", "ROXANA", "LIA", "RUTH", "NOELIA", "DEBORA", "CINTIA", "CINTHIA",
    "BELEN", "LOURDES", "ROCIO", "MILAGROS", "SOL", "LUCILA", "ANAHI", "ITATI", "PILAR",
    "ASUNCION", "CONCEPCION", "SOCORRO", "AMPARO", "CONSUELO", "ISABEL", "RAQUEL", "AIDA",
    "FANNY", "NIDIA", "EDITH", "MABEL", "EUGENIA", "LUDMILA", "MICAELA", "DAIANA", "GISELA",
    "GISELLE", "MAILEN", "MALENA", "GUADALUPE", "CANDELARIA", "CONSTANZA", "DELFORA", "LEOCADIA",
    "BENIGNA", "CLEMENTINA", "SILVINA", "GRICELDA", "YAMILA", "CELESTE", "IVANA", "VALENTINA"
})

_NOMBRES_MASCULINOS = frozenset({
    "JUAN", "JOSE", "CARLOS", "MIGUEL", "ANGEL", "JORGE", "LUIS", "HECTOR", "PEDRO", "OSCAR",
    "RUBEN", "DANIEL", "EDUARDO", "MARIO", "ROBERTO", "MANUEL", "ALBERTO", "GUILLERMO",
    "RAMON", "RICARDO", "ALEJANDRO", "HORACIO", "GUSTAVO", "WALTER", "SERGIO", "VICTOR",
    "HUGO", "FERNANDO", "MARCELO", "GABRIEL", "GONZALO", "LUCAS", "MATIAS", "NICOLAS",
    "CRISTIAN", "DIEGO", "PABLO", "SEBASTIAN", "MARTIN", "JAVIER", "ADRIAN", "FABIAN",
    "FEDERICO", "DAMIAN", "GERMAN", "LEONARDO", "ENRIQUE", "RODOLFO", "RAUL", "JULIO",
    "CESAR", "NESTOR", "ALFREDO", "OMAR", "EDGARDO", "DARIO", "MARCOS", "ANDRES", "TOMAS",
    "ARIEL", "AGUSTIN", "MAXIMILIANO", "FRANCO", "FACUNDO", "EMILIANO", "LEANDRO",
    "HERNAN", "EZEQUIEL", "MAURICIO", "RODRIGO", "SANTIAGO", "JOAQUIN", "IGNACIO",
    "EMILIO", "ERNESTO", "LEON", "FELIX", "ESTEBAN", "ANIBAL", "ARTURO", "BENJAMIN", "BERNARDO",
    "BRUNO", "CHRISTIAN", "CLAUDIO", "DANTE", "DAVID", "ELVIO", "ENZO", "EUGENIO",
    "EVER", "FABRICIO", "FAUSTINO", "FELIPE", "FERMIN", "GERARDO", "GUIDO", "JAIME", "JERONIMO",
    "JESUS", "LORENZO", "MARIANO", "NORBERTO", "ORLANDO", "PATRICIO", "RENE", "SALVADOR",
    "VALENTIN", "VICENTE", "WILFREDO", "ALDO", "AMADO", "AMERICO", "ARNALDO", "ATILIO",
    "BENIGNO", "BLAS", "CATALINO", "CIRILO", "CLEMENTE", "CRESCENCIO", "DAMASO", "DIONISIO",
    "ELEUTERIO", "ELPIDIO", "EPISFANIO", "EUSEBIO", "EUSTAQUIO", "EVARISTO", "FORTUNATO",
    "HILARIO", "HIPOLITO", "HONORIO", "IDELFONSO", "ISMAEL", "LADISLAO", "LEOCADIO", "LINO",
    "LISANDRO", "LUCIO", "MACARIO", "MODESTO", "NARCISO", "NEMESIO", "NICANOR", "OCTAVIO",
    "PLINIO", "PRIMITIVO", "QUINTIN", "RAMIRO", "REMIGIO", "ROGELIO", "ROMAN", "ROQUE",
    "RUFINO", "SABINO", "SATURNINO", "SEGUNDO", "SILVESTRE", "SIXTO", "TEODORO", "TIMOTEO",
    "TITO", "TORIBIO", "URBANO", "VENANCIO", "VITAL", "BRAIAN", "BRIAN", "ALEXIS", "AXEL",
    "KEVIN", "DYLAN", "IAN", "ALAN", "NAHUEL", "LAUTARO", "BAUTISTA", "NEHUEN", "SANTINO"
})

_APELLIDOS_EN_A = frozenset({
    "GARCIA", "SOSA", "CORDOBA", "OJEDA", "VEGA", "PEREA", "ZAVALIA", "BARRAZA", "ZARA",
    "DEZA", "MEDINA", "SILVA", "MOLINA", "LUNA", "LAMA", "VIERA", "OYOLA", "CEJAS", "DECIMA",
    "SEQUEIRA", "DORADO", "BONFIS", "SERRANO", "ENTRAIGAS", "AYALA", "PERALTA", "CHAVARRIA",
    "VILLA", "VERA", "ROCHA", "BARREDA", "ARCE", "MAZA", "GUERRA", "RIERA", "PALMA", "PLAZA",
    "ACOSTA", "RIVERA", "CHAVEZ", "ESTRADA", "PARRA", "TEJEDA", "BARRIOS", "MORALES", "ALVAREZ",
    "RODA", "MOTA", "COSTA", "SILVA", "FONTANA", "GUZMAN", "CASANOVA", "FIGUEROA", "TEJERINA"
})


def inferir_genero(nombre: str) -> str:
    """
    Infiere heurísticamente el género de una persona argentina a partir de su nombre.
    Retorna 'F' (Femenino) o 'M' (Masculino).
    """
    if not nombre:
        return "M"

    limpio = nombre.replace("-", " ").replace(",", " ").replace(".", " ")
    tokens = [t.strip().upper() for t in limpio.split() if len(t.strip()) > 1]
    if not tokens:
        return "M"

    # 1. Búsqueda exacta de tokens en nombres masculinos o femeninos
    for t in tokens:
        if t in _NOMBRES_MASCULINOS:
            return "M"
        if t in _NOMBRES_FEMENINOS:
            return "F"

    # 2. Regla morfológica: si el primer token termina en A/INA/ELA/ITA y no es apellido conocido
    primer_token = tokens[0]
    if primer_token.endswith(("A", "INA", "ELA", "ITA", "IA")) and primer_token not in _APELLIDOS_EN_A:
        return "F"

    # 3. Default estándar
    return "M"


def obtener_cuils_candidatos(dni: str, nombre: str = "", genero_hint: Optional[str] = None) -> Tuple[str, str]:
    """
    Genera la tupla de CUILs candidatos (candidato_primario, candidato_alternativo).
    Garantiza que siempre se disponga de ambas alternativas para verificación crediticia en BCRA.
    """
    if genero_hint and any(k in genero_hint.upper() for k in ("F", "FEM")):
        genero_primario = "F"
    elif genero_hint and any(k in genero_hint.upper() for k in ("M", "MASC")):
        genero_primario = "M"
    else:
        genero_primario = inferir_genero(nombre)

    genero_alternativo = "F" if genero_primario == "M" else "M"

    cuil_primario = calcular_cuil(dni, genero_primario)
    cuil_alternativo = calcular_cuil(dni, genero_alternativo)

    return cuil_primario, cuil_alternativo
