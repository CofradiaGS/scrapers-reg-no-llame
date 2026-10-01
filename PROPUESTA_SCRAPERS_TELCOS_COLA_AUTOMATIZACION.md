# Payloads Telcos: `cola_automatizacion`
**Flujo Cascada: Claro ➔ Personal ➔ Movistar ➔ Sin Coincidencias**

### Regla de Negocio
1. **Claro**: Consulta con Línea + DNI. Si encuentra deuda ➔ **Cortocircuito** (guarda y termina).
2. **Personal**: Si Claro no encuentra (o no hay DNI), consulta Personal con la Línea. Si encuentra deuda ➔ **Cortocircuito** (guarda y termina).
3. **Movistar**: Si ni Claro ni Personal encontraron, consulta Movistar (área + línea). Si encuentra deuda ➔ guarda y termina.
4. **Sin coincidencias**: Si ninguna tiene datos ➔ guarda las 3 consultas con `"status": "sin_coincidencias"` y `estado = 'completado'` en BD.

---

## 1. Coincidencia en Claro (Cortocircuito)

```json
{
  "status": "coincidencia",
  "operador": "Claro",
  "scrapers_intentados": ["claro"],
  "ultima_modificacion": "2026-09-29 10:45:12",
  "enacom": {
    "operador_origen": "Claro",
    "operador_oficial": "AMX ARGENTINA S.A.",
    "grupo_economico": "América Móvil (Claro)",
    "tipo_linea": "Móvil / Celular",
    "es_celular": true,
    "soporta_whatsapp": true,
    "servicio_oficial": "Servicio de Radiocomunicaciones Móviles Celulares",
    "modalidad": "CPP",
    "modalidad_descripcion": "Llamante Paga (Celular Convencional)",
    "codigo_area": "261",
    "bloque": "455",
    "prefijo_completo": "261455",
    "localidad_origen": "Mendoza",
    "provincia_origen": "Mendoza",
    "ani": "2614556677",
    "numero_local": "4556677",
    "numero_abonado": "6677",
    "formatos": {
      "e164": "+5492614556677",
      "whatsapp": "5492614556677",
      "nacional_celular": "0261 15-4556677"
    },
    "ultima_modificacion": "2026-09-29 10:45:10"
  },
  "claro": {
    "status": "coincidencia",
    "fuente": "claro",
    "operador": "Claro",
    "operador_receptor": "Claro",
    "titular": {
      "nombre": "",
      "apellido": "",
      "nro_documento": "33517690",
      "tipo_documento": "DNI",
      "cuit": "",
      "tipo_persona": "Persona Fisica"
    },
    "servicio": {
      "tecnologia": "Móvil / Celular",
      "producto": "CLARO",
      "modalidad_factura": "Modalidad 2876 (Cobro Express)"
    },
    "fechas": {
      "fecha_consulta": "2026-09-29T10:45:11.854210+00:00",
      "fecha_vencimiento": "15/10/2026"
    },
    "detalles": {
      "fuente_origen": "Cobro Express API",
      "id_empresa": 20916,
      "id_modalidad": 2876,
      "id_cliente": "2614556677",
      "codigo_barra_primario": "0209162876000045205020261015335176901",
      "deuda_total": 4520.50,
      "cantidad_comprobantes": 1,
      "comprobantes": [
        {
          "indice": 0,
          "idEmpresa": 20916,
          "nombreEmpresa": "CLARO",
          "idEmpresaModalidad": 2876,
          "codigoBarra": "0209162876000045205020261015335176901",
          "idCliente": "2614556677",
          "primerImporte": 4520.50,
          "segundoImporte": 4650.00,
          "importeMin": 0.0,
          "importeMax": 0.0,
          "idTipoMonto": 1,
          "detalles_item": [],
          "hash": "a1f9e8d7c6b5a43210fedcba98765432",
          "transaccionInfo_raw": "TxId=8847291;Term=WEB01;Seq=104",
          "transaccionInfo_decoded": {
            "TxId": "8847291",
            "Term": "WEB01",
            "Seq": "104"
          },
          "integradorInfo_raw": "ExpirationDate=15/10/2026;Account=2614556677;Doc=33517690",
          "integradorInfo_decoded": {
            "ExpirationDate": "15/10/2026",
            "Account": "2614556677",
            "Doc": "33517690"
          }
        }
      ],
      "dni_consultado": "33517690",
      "ani_consultado": "2614556677"
    },
    "raw": {
      "items": [
        {
          "idEmpresa": 20916,
          "nombreEmpresa": "CLARO",
          "idEmpresaModalidad": 2876,
          "codigoBarra": "0209162876000045205020261015335176901",
          "idCliente": "2614556677",
          "primerImporte": 4520.50,
          "segundoImporte": 4650.00
        }
      ]
    },
    "ultima_modificacion": "2026-09-29 10:45:12"
  }
}
```

---

## 2. Sin Coincidencia en Claro ➔ Coincidencia en Personal (Cortocircuito)

```json
{
  "status": "coincidencia",
  "operador": "Personal",
  "scrapers_intentados": ["claro", "personal"],
  "ultima_modificacion": "2026-09-29 10:45:14",
  "enacom": {
    "operador_origen": "Telecom Personal",
    "operador_oficial": "TELECOM ARGENTINA S.A.",
    "grupo_economico": "Telecom Argentina",
    "tipo_linea": "Móvil / Celular",
    "es_celular": true,
    "soporta_whatsapp": true,
    "servicio_oficial": "Servicio Móvil Avanzado",
    "modalidad": "CPP",
    "modalidad_descripcion": "Llamante Paga (Celular Convencional)",
    "codigo_area": "11",
    "bloque": "5544",
    "prefijo_completo": "115544",
    "localidad_origen": "Ciudad Autónoma de Buenos Aires",
    "provincia_origen": "Buenos Aires / CABA",
    "ani": "1155443322",
    "numero_local": "55443322",
    "numero_abonado": "3322",
    "formatos": {
      "e164": "+5491155443322",
      "whatsapp": "5491155443322",
      "nacional_celular": "011 15-55443322"
    },
    "ultima_modificacion": "2026-09-29 10:45:11"
  },
  "claro": {
    "status": "sin_coincidencia",
    "fuente": "claro",
    "operador": "Claro",
    "operador_receptor": "",
    "titular": {
      "nombre": "",
      "apellido": "",
      "nro_documento": "28445112",
      "tipo_documento": "DNI",
      "cuit": "",
      "tipo_persona": "Persona Fisica"
    },
    "servicio": {
      "tecnologia": "",
      "producto": "",
      "modalidad_factura": ""
    },
    "fechas": {},
    "detalles": {
      "http_status": 400,
      "error_message": "El cliente no existe o los datos ingresados son incorrectos",
      "dni_consultado": "28445112",
      "ani_consultado": "1155443322"
    },
    "raw": {
      "status_code": 400,
      "response": "{\"message\":\"El cliente no existe o los datos ingresados son incorrectos\"}"
    },
    "ultima_modificacion": "2026-09-29 10:45:12"
  },
  "personal": {
    "status": "coincidencia",
    "fuente": "personal",
    "operador": "Personal",
    "operador_receptor": "Personal",
    "titular": {
      "nombre": "",
      "apellido": "",
      "nro_documento": "28445112",
      "tipo_documento": "DNI",
      "cuit": "",
      "tipo_persona": "Persona Fisica"
    },
    "servicio": {
      "tecnologia": "Móvil / Celular",
      "producto": "TELECOM PERSONAL",
      "modalidad_factura": "Modalidad 2864 (Cobro Express)"
    },
    "fechas": {
      "fecha_consulta": "2026-09-29T10:45:13.912401+00:00",
      "fecha_vencimiento": "10/10/2026"
    },
    "detalles": {
      "fuente_origen": "Cobro Express API",
      "id_empresa": 20910,
      "id_modalidad": 2864,
      "id_cliente": "1155443322",
      "codigo_barra_primario": "020910286400003120002026101011554433220",
      "deuda_total": 3120.00,
      "cantidad_comprobantes": 1,
      "comprobantes": [
        {
          "idEmpresa": 20910,
          "nombreEmpresa": "TELECOM PERSONAL",
          "idEmpresaModalidad": 2864,
          "codigoBarra": "020910286400003120002026101011554433220",
          "idCliente": "1155443322",
          "primerImporte": 3120.00,
          "segundoImporte": 3250.00
        }
      ],
      "comprobantes_enriquecidos": [
        {
          "indice": 0,
          "idEmpresa": 20910,
          "nombreEmpresa": "TELECOM PERSONAL",
          "idEmpresaModalidad": 2864,
          "codigoBarra": "020910286400003120002026101011554433220",
          "idCliente": "1155443322",
          "primerImporte": 3120.00,
          "segundoImporte": 3250.00,
          "importeMin": 0.0,
          "importeMax": 0.0,
          "idTipoMonto": 1,
          "detalles_item": [],
          "hash": "b2e4f6a8c0d2e4f6a8c0d2e4f6a8c0d2",
          "transaccionInfo_raw": "TxId=9918234;Term=WEB02;Seq=205",
          "transaccionInfo_decoded": {
            "TxId": "9918234",
            "Term": "WEB02",
            "Seq": "205"
          },
          "integradorInfo_raw": "ExpirationDate=10/10/2026;Line=1155443322",
          "integradorInfo_decoded": {
            "ExpirationDate": "10/10/2026",
            "Line": "1155443322"
          }
        }
      ],
      "ani_consultado": "1155443322",
      "dni_consultado": "28445112"
    },
    "raw": [
      {
        "idEmpresa": 20910,
        "nombreEmpresa": "TELECOM PERSONAL",
        "idEmpresaModalidad": 2864,
        "codigoBarra": "020910286400003120002026101011554433220",
        "idCliente": "1155443322",
        "primerImporte": 3120.00,
        "segundoImporte": 3250.00
      }
    ],
    "ultima_modificacion": "2026-09-29 10:45:14"
  }
}
```

---

## 3. Sin Coincidencia en Claro ni Personal ➔ Coincidencia en Movistar

```json
{
  "status": "coincidencia",
  "operador": "Movistar",
  "scrapers_intentados": ["claro", "personal", "movistar"],
  "ultima_modificacion": "2026-09-29 10:45:16",
  "enacom": {
    "operador_origen": "Movistar",
    "operador_oficial": "TELEFONICA MOVILES ARGENTINA S.A.",
    "grupo_economico": "Telefónica Hispanoamérica (Movistar)",
    "tipo_linea": "Móvil / Celular",
    "es_celular": true,
    "soporta_whatsapp": true,
    "servicio_oficial": "Servicio de Radiocomunicaciones Móviles Celulares",
    "modalidad": "CPP",
    "modalidad_descripcion": "Llamante Paga (Celular Convencional)",
    "codigo_area": "341",
    "bloque": "677",
    "prefijo_completo": "341677",
    "localidad_origen": "Rosario",
    "provincia_origen": "Santa Fe",
    "ani": "3416778899",
    "numero_local": "6778899",
    "numero_abonado": "8899",
    "formatos": {
      "e164": "+5493416778899",
      "whatsapp": "5493416778899",
      "nacional_celular": "0341 15-6778899"
    },
    "ultima_modificacion": "2026-09-29 10:45:11"
  },
  "claro": {
    "status": "sin_coincidencia",
    "fuente": "claro",
    "operador": "Claro",
    "operador_receptor": "",
    "titular": {
      "nombre": "",
      "apellido": "",
      "nro_documento": "36123456",
      "tipo_documento": "DNI",
      "cuit": "",
      "tipo_persona": "Persona Fisica"
    },
    "servicio": {
      "tecnologia": "",
      "producto": "",
      "modalidad_factura": ""
    },
    "fechas": {},
    "detalles": {
      "http_status": 400,
      "error_message": "El cliente no existe o los datos ingresados son incorrectos",
      "dni_consultado": "36123456",
      "ani_consultado": "3416778899"
    },
    "raw": {
      "status_code": 400,
      "response": "{\"message\":\"El cliente no existe o los datos ingresados son incorrectos\"}"
    },
    "ultima_modificacion": "2026-09-29 10:45:12"
  },
  "personal": {
    "status": "sin_coincidencia",
    "fuente": "personal",
    "operador": "Personal",
    "operador_receptor": "",
    "titular": {
      "nombre": "",
      "apellido": "",
      "nro_documento": "36123456",
      "tipo_documento": "DNI",
      "cuit": "",
      "tipo_persona": "Persona Fisica"
    },
    "servicio": {
      "tecnologia": "",
      "producto": "",
      "modalidad_factura": ""
    },
    "fechas": {},
    "detalles": {
      "http_status": 400,
      "error_message": "No se encontraron comprobantes pendientes",
      "ani_consultado": "3416778899"
    },
    "raw": {
      "status_code": 400,
      "message": "No se encontraron comprobantes pendientes"
    },
    "ultima_modificacion": "2026-09-29 10:45:14"
  },
  "movistar": {
    "status": "coincidencia",
    "fuente": "movistar",
    "operador": "Movistar",
    "operador_receptor": "Movistar",
    "titular": {
      "nombre": "",
      "apellido": "",
      "nro_documento": "36123456",
      "tipo_documento": "DNI",
      "cuit": "",
      "tipo_persona": "Persona Fisica"
    },
    "servicio": {
      "tecnologia": "Móvil / Celular",
      "producto": "MOVISTAR",
      "modalidad_factura": "Modalidad 2843 (Cobro Express)"
    },
    "fechas": {
      "fecha_consulta": "2026-09-29T10:45:15.789123+00:00",
      "fecha_vencimiento": "12/10/2026"
    },
    "detalles": {
      "fuente_origen": "Cobro Express API",
      "id_empresa": 20908,
      "id_modalidad": 2843,
      "id_cliente": "3416778899",
      "codigo_barra_primario": "020908284300005200002026101234167788995",
      "deuda_total": 5200.00,
      "cantidad_comprobantes": 1,
      "comprobantes": [
        {
          "indice": 0,
          "idEmpresa": 20908,
          "nombreEmpresa": "MOVISTAR",
          "idEmpresaModalidad": 2843,
          "codigoBarra": "020908284300005200002026101234167788995",
          "idCliente": "3416778899",
          "primerImporte": 5200.00,
          "segundoImporte": 5350.00,
          "importeMin": 0.0,
          "importeMax": 0.0,
          "idTipoMonto": 1,
          "detalles_item": [],
          "hash": "c3d5e7f9a1b3c5d7e9f1a3b5c7d9e1f3",
          "transaccionInfo_raw": "TxId=7721839;Term=WEB03;Seq=301",
          "transaccionInfo_decoded": {
            "TxId": "7721839",
            "Term": "WEB03",
            "Seq": "301"
          },
          "integradorInfo_raw": "ExpirationDate=12/10/2026;Line=6778899;Area=341",
          "integradorInfo_decoded": {
            "ExpirationDate": "12/10/2026",
            "Line": "6778899",
            "Area": "341"
          }
        }
      ],
      "caracteristica": "341",
      "numero_local": "6778899",
      "region": "Santa Fe Interior",
      "ani_consultado": "3416778899",
      "dni_consultado": "36123456"
    },
    "raw": {
      "items": [
        {
          "idEmpresa": 20908,
          "nombreEmpresa": "MOVISTAR",
          "idEmpresaModalidad": 2843,
          "codigoBarra": "020908284300005200002026101234167788995",
          "idCliente": "3416778899",
          "primerImporte": 5200.00,
          "segundoImporte": 5350.00
        }
      ]
    },
    "ultima_modificacion": "2026-09-29 10:45:16"
  }
}
```

---

## 4. Sin Coincidencias en Ninguna Operadora

```json
{
  "status": "sin_coincidencias",
  "operador": null,
  "descripcion": "Sin coincidencias en Claro, Personal ni Movistar",
  "scrapers_intentados": ["claro", "personal", "movistar"],
  "ultima_modificacion": "2026-09-29 10:45:18",
  "enacom": {
    "operador_origen": "Claro",
    "operador_oficial": "AMX ARGENTINA S.A.",
    "grupo_economico": "América Móvil (Claro)",
    "tipo_linea": "Móvil / Celular",
    "es_celular": true,
    "soporta_whatsapp": true,
    "servicio_oficial": "Servicio de Radiocomunicaciones Móviles Celulares",
    "modalidad": "CPP",
    "modalidad_descripcion": "Llamante Paga (Celular Convencional)",
    "codigo_area": "381",
    "bloque": "411",
    "prefijo_completo": "381411",
    "localidad_origen": "San Miguel de Tucumán",
    "provincia_origen": "Tucumán",
    "ani": "3814112233",
    "numero_local": "4112233",
    "numero_abonado": "2233",
    "formatos": {
      "e164": "+5493814112233",
      "whatsapp": "5493814112233",
      "nacional_celular": "0381 15-4112233"
    },
    "ultima_modificacion": "2026-09-29 10:45:11"
  },
  "claro": {
    "status": "sin_coincidencia",
    "fuente": "claro",
    "operador": "Claro",
    "operador_receptor": "",
    "titular": {
      "nombre": "",
      "apellido": "",
      "nro_documento": "39887766",
      "tipo_documento": "DNI",
      "cuit": "",
      "tipo_persona": "Persona Fisica"
    },
    "servicio": {
      "tecnologia": "",
      "producto": "",
      "modalidad_factura": ""
    },
    "fechas": {},
    "detalles": {
      "http_status": 400,
      "error_message": "El cliente no existe o los datos ingresados son incorrectos",
      "dni_consultado": "39887766",
      "ani_consultado": "3814112233"
    },
    "raw": {
      "status_code": 400,
      "response": "{\"message\":\"El cliente no existe o los datos ingresados son incorrectos\"}"
    },
    "ultima_modificacion": "2026-09-29 10:45:13"
  },
  "personal": {
    "status": "sin_coincidencia",
    "fuente": "personal",
    "operador": "Personal",
    "operador_receptor": "",
    "titular": {
      "nombre": "",
      "apellido": "",
      "nro_documento": "39887766",
      "tipo_documento": "DNI",
      "cuit": "",
      "tipo_persona": "Persona Fisica"
    },
    "servicio": {
      "tecnologia": "",
      "producto": "",
      "modalidad_factura": ""
    },
    "fechas": {},
    "detalles": {
      "http_status": 400,
      "error_message": "No se encontraron comprobantes pendientes",
      "ani_consultado": "3814112233"
    },
    "raw": {
      "status_code": 400,
      "message": "No se encontraron comprobantes pendientes"
    },
    "ultima_modificacion": "2026-09-29 10:45:15"
  },
  "movistar": {
    "status": "sin_coincidencia",
    "fuente": "movistar",
    "operador": "Movistar",
    "operador_receptor": "",
    "titular": {
      "nombre": "",
      "apellido": "",
      "nro_documento": "39887766",
      "tipo_documento": "DNI",
      "cuit": "",
      "tipo_persona": "Persona Fisica"
    },
    "servicio": {
      "tecnologia": "",
      "producto": "",
      "modalidad_factura": ""
    },
    "fechas": {},
    "detalles": {
      "http_status": 400,
      "error_message": "Línea o cuenta inexistente en Movistar",
      "caracteristica": "381",
      "numero_local": "4112233",
      "region": "Tucumán",
      "ani_consultado": "3814112233"
    },
    "raw": {
      "status_code": 400,
      "response": "{\"message\":\"Línea o cuenta inexistente en Movistar\"}"
    },
    "ultima_modificacion": "2026-09-29 10:45:17"
  }
}
```

---

### Comando de Ejecución (Supervisor)
```powershell
python supervisor_vps.py --queue cola_automatizacion --auto-id telco_scraper --scraper telcos --workers 8 --proxy-pool
```
