# -*- coding: utf-8 -*-
import os
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).parent.parent

HTML_CONTENT = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>Informe Gerencial de Scraping - Septiembre 2026</title>
<style>
  @page {
    size: A4;
    margin: 14mm 16mm 14mm 16mm;
    @bottom-right {
      content: counter(page);
    }
  }

  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    color: #1e293b;
    line-height: 1.45;
    font-size: 8.8pt;
    margin: 0;
    padding: 0;
  }

  .header-card {
    background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 100%);
    color: white;
    padding: 20px 24px;
    border-radius: 8px;
    margin-bottom: 16px;
  }

  .header-card h1 {
    font-size: 17pt;
    margin: 0 0 4px 0;
    font-weight: 700;
    letter-spacing: -0.3px;
  }

  .header-card .subtitle {
    font-size: 10pt;
    color: #93c5fd;
    font-weight: 400;
    margin: 0 0 12px 0;
  }

  .meta-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 6px 20px;
    font-size: 8.2pt;
    border-top: 1px solid rgba(255, 255, 255, 0.18);
    padding-top: 10px;
  }

  .meta-item strong {
    color: #cbd5e1;
  }

  h2 {
    font-size: 11.5pt;
    color: #0f172a;
    border-bottom: 2px solid #e2e8f0;
    padding-bottom: 5px;
    margin-top: 18px;
    margin-bottom: 10px;
    font-weight: 700;
    display: flex;
    align-items: center;
  }

  h3 {
    font-size: 9.5pt;
    color: #1e3a8a;
    margin-top: 12px;
    margin-bottom: 6px;
    font-weight: 700;
  }

  .section-tag {
    background: #0284c7;
    color: white;
    font-size: 7.5pt;
    padding: 2px 6px;
    border-radius: 4px;
    margin-right: 8px;
    font-weight: 600;
    text-transform: uppercase;
  }

  p {
    margin: 0 0 8px 0;
    text-align: justify;
  }

  table {
    width: 100%;
    border-collapse: collapse;
    margin: 10px 0 14px 0;
    font-size: 8.2pt;
  }

  th {
    background-color: #f1f5f9;
    color: #334155;
    font-weight: 600;
    text-align: left;
    padding: 6px 8px;
    border: 1px solid #cbd5e1;
    font-size: 7.8pt;
    text-transform: uppercase;
    letter-spacing: 0.3px;
  }

  td {
    padding: 5px 8px;
    border: 1px solid #e2e8f0;
    vertical-align: middle;
  }

  tr:nth-child(even) td {
    background-color: #f8fafc;
  }

  .badge-green {
    background-color: #dcfce7;
    color: #15803d;
    padding: 2px 6px;
    border-radius: 4px;
    font-weight: 600;
    font-size: 7.5pt;
    display: inline-block;
  }

  .badge-blue {
    background-color: #e0f2fe;
    color: #0369a1;
    padding: 2px 6px;
    border-radius: 4px;
    font-weight: 600;
    font-size: 7.5pt;
    display: inline-block;
  }

  .badge-orange {
    background-color: #ffedd5;
    color: #c2410c;
    padding: 2px 6px;
    border-radius: 4px;
    font-weight: 700;
    font-size: 7.5pt;
    display: inline-block;
  }

  .badge-neutral {
    background-color: #f1f5f9;
    color: #475569;
    padding: 2px 6px;
    border-radius: 4px;
    font-weight: 600;
    font-size: 7.5pt;
    display: inline-block;
  }

  .text-right {
    text-align: right;
  }

  .text-center {
    text-align: center;
  }

  .highlight-box {
    background-color: #eff6ff;
    border-left: 4px solid #3b82f6;
    padding: 8px 12px;
    margin: 8px 0 12px 0;
    border-radius: 0 6px 6px 0;
    font-size: 8.4pt;
  }

  .kpi-row {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 10px;
    margin: 10px 0 14px 0;
  }

  .kpi-card {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 6px;
    padding: 8px 12px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.03);
  }

  .kpi-label {
    font-size: 7.2pt;
    color: #64748b;
    text-transform: uppercase;
    font-weight: 600;
    margin-bottom: 2px;
  }

  .kpi-value {
    font-size: 13.5pt;
    font-weight: 700;
    color: #0f172a;
    margin-bottom: 2px;
  }

  .kpi-sub {
    font-size: 7pt;
    color: #16a34a;
    font-weight: 600;
  }

  .scraper-profile {
    border: 1px solid #e2e8f0;
    border-radius: 6px;
    padding: 8px 12px;
    margin-bottom: 10px;
    background-color: #ffffff;
  }

  .scraper-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 6px;
    border-bottom: 1px solid #f1f5f9;
    padding-bottom: 4px;
  }

  .scraper-title {
    font-size: 9.5pt;
    font-weight: 700;
    color: #0f172a;
  }

  .scraper-desc-grid {
    display: grid;
    grid-template-columns: 110px 1fr;
    gap: 4px 10px;
    font-size: 8.2pt;
  }

  .scraper-desc-label {
    font-weight: 600;
    color: #475569;
  }

  .scraper-desc-value {
    color: #1e293b;
  }

  .flow-step-box {
    background: #f8fafc;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 8px 12px;
    margin-bottom: 8px;
  }

  .flow-step-header {
    font-weight: 700;
    color: #0f172a;
    display: flex;
    justify-content: space-between;
    margin-bottom: 4px;
    font-size: 8.5pt;
  }

  .flow-step-body {
    font-size: 8pt;
    color: #334155;
  }

  .page-break {
    page-break-after: always;
  }

  ul {
    margin: 4px 0 8px 16px;
    padding: 0;
  }

  li {
    margin-bottom: 3px;
  }

  .footer-note {
    font-size: 7.5pt;
    color: #94a3b8;
    border-top: 1px solid #e2e8f0;
    padding-top: 6px;
    margin-top: 16px;
    text-align: center;
  }
</style>
</head>
<body>

  <!-- ENCABEZADO EJECUTIVO -->
  <div class="header-card">
    <h1>INFORME GERENCIAL Y TÉCNICO DE SCRAPING</h1>
    <div class="subtitle">Desempeño Operativo Septiembre vs. Agosto 2026, Arquitectura de Cascada y Catálogo de Motores</div>
    <div class="meta-grid">
      <div class="meta-item"><strong>Fecha de Emisión:</strong> 08 de Octubre de 2026</div>
      <div class="meta-item"><strong>Perfil del Documento:</strong> Gerencial / Dirección de Operaciones y Tecnología</div>
      <div class="meta-item"><strong>Alcance de Bases:</strong> Registro No Llame &amp; Cola de Automatización General</div>
      <div class="meta-item"><strong>Nuevas Incorporaciones:</strong> ENACOM Web (OCR), Telecentro &amp; Power CRM FTTH</div>
    </div>
  </div>

  <!-- KPI SUMMARY -->
  <div class="kpi-row">
    <div class="kpi-card">
      <div class="kpi-label">Reg. No Llame (Sep)</div>
      <div class="kpi-value">48,655</div>
      <div class="kpi-sub">+100.0% vs Agosto (0)</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">Cola Auto (Completados)</div>
      <div class="kpi-value">1,188,031</div>
      <div class="kpi-sub">+260.9% (x3.6 veces)</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">Cola Auto (Ingresados)</div>
      <div class="kpi-value">13.40 M</div>
      <div class="kpi-sub">+486.9% vs Agosto (2.28 M)</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">Total Resueltos</div>
      <div class="kpi-value">1,236,686</div>
      <div class="kpi-sub">+275.6% vs Agosto (329K)</div>
    </div>
  </div>

  <!-- 1. RESUMEN EJECUTIVO -->
  <h2><span class="section-tag">01</span> Resumen Ejecutivo y Comparativa de Volumen</h2>
  <p>
    Durante el mes de <strong>septiembre de 2026</strong>, la plataforma de extracción experimentó una aceleración histórica en capacidad y volumen de procesamiento, alcanzando <strong>1,236,686 de registros resueltos</strong> frente a los <strong>329,222</strong> de agosto (un incremento neto del <strong>+275.6%</strong>).
  </p>
  <p>
    Este crecimiento se fundamentó en cuatro pilares operacionales:
  </p>
  <ul>
    <li><strong>Inauguración Productiva de Registro No Llame:</strong> La base acumulaba más de 3.1 millones de líneas sin procesar durante agosto (<strong>0 consultas en agosto</strong>). En septiembre se lanzó el operativo continuo resolviendo <strong>48,655 líneas</strong>.</li>
    <li><strong>Ampliación de Capacidad en IRIS:</strong> Se incorporó una nueva cuenta corporativa de alta capacidad dedicada exclusivamente a soportar el tráfico de Registro No Llame sin saturar las operaciones habituales.</li>
    <li><strong>Despliegue de Nuevos Motores de Extracción:</strong> Se integró el scraper en vivo de <strong>ENACOM Web</strong> (con tecnología OCR de resolución de CAPTCHA sin costo de APIs externas para certificar portabilidad en tiempo real), el motor de <strong>Telecentro</strong> y el validador de cobertura de fibra óptica <strong>Power CRM FTTH</strong>.</li>
    <li><strong>Optimización por Cascada y Cortocircuito:</strong> Se logró un ahorro del 70% en llamadas redundantes a las operadoras, multiplicando por 3.6 el volumen completado en la cola de automatización general.</li>
  </ul>

  <table>
    <thead>
      <tr>
        <th>Dimensión Operativa</th>
        <th class="text-center">Agosto 2026</th>
        <th class="text-center">Septiembre 2026</th>
        <th class="text-center">Variación Neta</th>
        <th class="text-center">Incremento (%)</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td><strong>Registro No Llame (Resueltos / Finalizados)</strong></td>
        <td class="text-center">0</td>
        <td class="text-center"><strong>48,655</strong></td>
        <td class="text-center">+48,655</td>
        <td class="text-center"><span class="badge-green">+100.0%</span> <em>(Nuevo inicio)</em></td>
      </tr>
      <tr>
        <td>&nbsp;&nbsp;&nbsp;↳ <em>Finalizados con Coincidencia Positiva (Titular confirmado)</em></td>
        <td class="text-center">0</td>
        <td class="text-center">43,222</td>
        <td class="text-center">+43,222</td>
        <td class="text-center"><span class="badge-neutral">—</span></td>
      </tr>
      <tr>
        <td>&nbsp;&nbsp;&nbsp;↳ <em>Finalizados sin Coincidencia (Barrido total)</em></td>
        <td class="text-center">0</td>
        <td class="text-center">5,433</td>
        <td class="text-center">+5,433</td>
        <td class="text-center"><span class="badge-neutral">—</span></td>
      </tr>
      <tr>
        <td><strong>Cola de Automatización (Ingresados a cola)</strong></td>
        <td class="text-center">2,283,014</td>
        <td class="text-center"><strong>13,400,456</strong></td>
        <td class="text-center">+11,117,442</td>
        <td class="text-center"><span class="badge-green">+486.9% (x5.8)</span></td>
      </tr>
      <tr>
        <td><strong>Cola de Automatización (Completados exitosos)</strong></td>
        <td class="text-center">329,222</td>
        <td class="text-center"><strong>1,188,031</strong></td>
        <td class="text-center">+858,809</td>
        <td class="text-center"><span class="badge-green">+260.9% (x3.6)</span></td>
      </tr>
      <tr style="font-weight: 700; background-color: #f1f5f9;">
        <td>TOTAL GENERAL REGISTROS PROCESADOS Y RESUELTOS</td>
        <td class="text-center">329,222</td>
        <td class="text-center">1,236,686</td>
        <td class="text-center">+907,464</td>
        <td class="text-center"><span class="badge-green">+275.6% (x3.7)</span></td>
      </tr>
    </tbody>
  </table>

  <!-- 2. REGISTRO NO LLAME -->
  <h2><span class="section-tag">02</span> Base Registro No Llame (queue_registro_no_llame)</h2>
  <div class="highlight-box">
    <strong>Contexto Operativo:</strong> La tabla contaba con 3,172,798 registros ingresados a finales de julio, permaneciendo con <strong>actividad nula durante todo agosto (0 registros procesados)</strong>. En septiembre se puso en marcha el operativo continuo resolviendo 48,655 líneas en su ciclo completo mediante la nueva cuenta corporativa y la cascada multi-operador.
  </div>

  <table>
    <thead>
      <tr>
        <th>Estado en Pipeline</th>
        <th>Scraper Actual</th>
        <th class="text-center">Cantidad Registros</th>
        <th class="text-center">% Resueltos</th>
        <th>Comportamiento Técnico</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td><strong>completado</strong></td>
        <td><code>finalizado</code></td>
        <td class="text-center"><strong>43,222</strong></td>
        <td class="text-center">88.8%</td>
        <td>Línea validada y titular confirmado por cortocircuito (IRIS o Telcos).</td>
      </tr>
      <tr>
        <td><strong>no_coincidencia</strong></td>
        <td><code>finalizado</code></td>
        <td class="text-center"><strong>5,433</strong></td>
        <td class="text-center">11.2%</td>
        <td>Línea que agotó la totalidad de scrapers sin coincidencia activa.</td>
      </tr>
      <tr style="font-weight: 600; background-color: #f8fafc;">
        <td colspan="2">TOTAL RESUELTOS EN SEPTIEMBRE</td>
        <td class="text-center"><strong>48,655</strong></td>
        <td class="text-center"><strong>100.0%</strong></td>
        <td><strong>Total de líneas que completaron el ciclo en septiembre.</strong></td>
      </tr>
      <tr>
        <td><em>completado (intermedio)</em></td>
        <td><code>personal</code></td>
        <td class="text-center">9,663</td>
        <td class="text-center">—</td>
        <td>Enriquecido con datos de Personal Flow durante el pipeline.</td>
      </tr>
      <tr>
        <td><em>completado (intermedio)</em></td>
        <td><code>claro</code></td>
        <td class="text-center">3,790</td>
        <td class="text-center">—</td>
        <td>Enriquecido con datos de Claro Argentina durante el pipeline.</td>
      </tr>
      <tr>
        <td><em>pendiente (en cola)</em></td>
        <td><code>iris</code></td>
        <td class="text-center">2,986,435</td>
        <td class="text-center">—</td>
        <td>Stock disponible en inventario para próximos ciclos.</td>
      </tr>
    </tbody>
  </table>

  <!-- 3. COLA AUTOMATIZACION -->
  <h2><span class="section-tag">03</span> Cola General de Automatización (cola_automatizacion)</h2>
  <table>
    <thead>
      <tr>
        <th>Motor de Scraping (auto_id)</th>
        <th class="text-center">Completados Ago 2026</th>
        <th class="text-center">Completados Sep 2026</th>
        <th class="text-center">Variación (%)</th>
        <th>Rol y Alcance</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td><strong>scraper_cobro_express</strong> <em>(Telcos)</em></td>
        <td class="text-center">188,422</td>
        <td class="text-center"><strong>624,678</strong></td>
        <td class="text-center"><span class="badge-green">+231.5%</span></td>
        <td>Validación Claro / Personal / Movistar vía Cobro Express.</td>
      </tr>
      <tr>
        <td><strong>scraper_power_crm</strong></td>
        <td class="text-center">22,183</td>
        <td class="text-center"><strong>264,322</strong></td>
        <td class="text-center"><span class="badge-green">+1,091.5%</span></td>
        <td>Enriquecimiento y cruce de cartera Power CRM.</td>
      </tr>
      <tr>
        <td><strong>scraper_cuit_online</strong></td>
        <td class="text-center">68,428</td>
        <td class="text-center"><strong>120,433</strong></td>
        <td class="text-center"><span class="badge-green">+76.0%</span></td>
        <td>Extracción fiscal de DNI / CUIT / Razón Social.</td>
      </tr>
      <tr>
        <td><strong>iris_scraper</strong></td>
        <td class="text-center">0</td>
        <td class="text-center"><strong>101,224</strong></td>
        <td class="text-center"><span class="badge-blue">+100.0% (Nuevo)</span></td>
        <td><strong>[NUEVO en cola general]</strong> Consultas Movistar BPM.</td>
      </tr>
      <tr>
        <td><strong>scraper_power_crm_ftth</strong></td>
        <td class="text-center">0</td>
        <td class="text-center"><strong>64,997</strong></td>
        <td class="text-center"><span class="badge-orange">+100.0% (Nuevo)</span></td>
        <td><strong>[NUEVO en septiembre]</strong> Factibilidad de fibra óptica.</td>
      </tr>
      <tr>
        <td><strong>bcra_scraper</strong></td>
        <td class="text-center">26,535</td>
        <td class="text-center"><strong>12,377</strong></td>
        <td class="text-center"><span class="badge-neutral">-53.4%</span></td>
        <td>Situación crediticia e historial de cheques BCRA.</td>
      </tr>
      <tr>
        <td><strong>scraper_datuar</strong></td>
        <td class="text-center">23,654</td>
        <td class="text-center"><em>En cascada</em></td>
        <td class="text-center"><span class="badge-neutral">—</span></td>
        <td>Padrón telefónico y vínculos de personas físicas.</td>
      </tr>
      <tr style="font-weight: 700; background-color: #f1f5f9;">
        <td>TOTAL COMPLETADOS</td>
        <td class="text-center">329,222</td>
        <td class="text-center">1,188,031</td>
        <td class="text-center"><span class="badge-green">+260.9%</span></td>
        <td>Crecimiento neto de +858,809 registros completados.</td>
      </tr>
    </tbody>
  </table>

  <div class="page-break"></div>

  <!-- 4. COMO FUNCIONA LA CASCADA Y EL CORTOCIRCUITO -->
  <h2><span class="section-tag">04</span> ¿Cómo Funciona la Cascada y la Regla del Cortocircuito?</h2>
  
  <h3>1. ¿Por qué existe una Cascada? (Estrategia de Eficiencia y Costo Cero)</h3>
  <p>
    En una base masiva de millones de teléfonos, consultar a todas las operadoras simultáneamente por cada número provocaría una saturación innecesaria de red, dispararía las probabilidades de bloqueos preventivos por parte de los proveedores y quintuplicaría el tiempo de espera.
  </p>
  <p>
    Para evitarlo, la arquitectura opera bajo una <strong>Cadena de Responsabilidad o Cascada</strong>: el sistema consulta a los operadores uno tras otro, siguiendo una secuencia estrictamente optimizada por probabilidad de éxito.
  </p>

  <h3>2. El Recorrido Paso a Paso de la Cascada</h3>
  
  <div class="flow-step-box">
    <div class="flow-step-header">
      <span>1° ESLABÓN — Movistar BPM (IRIS)</span>
      <span class="badge-blue">RESOLUCIÓN INMEDIATA</span>
    </div>
    <div class="flow-step-body">
      Es la fuente más completa y veloz. Consulta si la línea pertenece a Movistar o si sufrió una migración (evento <em>Port-Out</em> hacia otra compañía). Si la línea pertenece a Movistar, se extrae el titular, DNI y operaciones, resolviendo la línea en el acto sin consultar a Claro ni Personal.
    </div>
  </div>

  <div class="flow-step-box">
    <div class="flow-step-header">
      <span>2° ESLABÓN — Auditoría Claro (Cobro Express)</span>
      <span class="badge-green">CONDICIÓN: REQUIERE DNI</span>
    </div>
    <div class="flow-step-body">
      Si en el paso anterior se detectó que la línea migró fuera de Movistar y se dispone del DNI de la persona, el sistema consulta a Claro mediante la pasarela Cobro Express (Línea + DNI). Si hay factura impaga o deuda activa ➔ <strong>CORTOCIRCUITO ⚡ (Línea Resuelta)</strong>.
    </div>
  </div>

  <div class="flow-step-box">
    <div class="flow-step-header">
      <span>3° ESLABÓN — Auditoría Personal (Telecom Flow)</span>
      <span class="badge-blue">MODALIDAD CBF (LÍNEA SOLA)</span>
    </div>
    <div class="flow-step-body">
      Si Claro no registra deuda o no se disponía del DNI, la consulta pasa automáticamente a Telecom Personal. Este operador admite consultas bajo modalidad CBF (consulta directa por línea sin exigir DNI previo). Si hay deuda o línea activa ➔ <strong>CORTOCIRCUITO ⚡ (Línea Resuelta)</strong>.
    </div>
  </div>

  <div class="flow-step-box">
    <div class="flow-step-header">
      <span>4° ESLABÓN — Movistar Cobros Alternativo</span>
      <span class="badge-neutral">CANAL SECUNDARIO</span>
    </div>
    <div class="flow-step-body">
      Si ni Claro ni Personal registraron deuda o factura activa, se consulta el canal alternativo de Movistar por código de área y línea para descartar deudas vigentes fuera del portal BPM.
    </div>
  </div>

  <div class="flow-step-box">
    <div class="flow-step-header">
      <span>5° ESLABÓN — Cierre o Enriquecimiento de Identidad</span>
      <span class="badge-neutral">PERFIL 360°</span>
    </div>
    <div class="flow-step-body">
      Si ninguna de las telcos arrojó coincidencia, se marca como <em>Sin Coincidencia Telco</em>. Si se confirmó la titularidad y se dispone de DNI, el registro avanza hacia los enriquecedores de identidad (Datuar, CUITOnline y BCRA).
    </div>
  </div>

  <h3>3. La Regla del Cortocircuito (Short-Circuit ⚡)</h3>
  <div class="highlight-box">
    <strong>Definición del Cortocircuito:</strong> En el instante exacto en que cualquiera de los operadores (Claro, Personal o Movistar) confirma una coincidencia positiva (factura impaga, deuda vigente o línea activa confirmada), el sistema <strong>aborta de inmediato el resto de las consultas de la cascada</strong>.
    <br><br>
    <strong>Impacto Operativo:</strong> Si la línea es resuelta en Claro (primer salto), se omiten el 100% de las consultas a Personal y Movistar. Esto reduce la latencia de respuesta de ~6.5 segundos a solo ~1.4 segundos por línea y ahorra más del 70% del tráfico total hacia los servidores externos.
  </div>

  <div class="page-break"></div>

  <!-- 5. ENRIQUECIMIENTO DE DATOS PERSONALES -->
  <h2><span class="section-tag">05</span> Flujo de Enriquecimiento de Datos Personales (Perfil 360°)</h2>
  <p>
    Una vez determinada la línea y el DNI a través de las telcos o el repositorio central, se dispara el pipeline de <strong>Enriquecimiento de Identidad Fiscal y Crediticia</strong>. Emplea un despachador paralelo desacoplado que alimenta simultáneamente los namespaces de CUITOnline y BCRA:
  </p>

  <div class="flow-step-box">
    <div class="flow-step-header">
      <span>PASO 1: Validación de Identidad Base</span>
      <span class="badge-neutral">FILTRO OBLIGATORIO</span>
    </div>
    <div class="flow-step-body">
      Requiere DNI preexistente (proveniente del titular en IRIS, de la base madre o de BigQuery). Sin DNI no es viable la derivación tributaria y se cierra el circuito.
    </div>
  </div>

  <div class="flow-step-box">
    <div class="flow-step-header">
      <span>PASO 2: Extracción de CUIT y Vínculos vía DATUAR</span>
      <span class="badge-blue">PADRÓN NACIONAL</span>
    </div>
    <div class="flow-step-body">
      Si no fue consultado previamente, Datuar extrae el CUIT/CUIL formal, domicilios históricos, vínculos familiares y actividad declarada. Si no se obtiene un CUIT confiable, se detiene el avance crediticio.
    </div>
  </div>

  <div class="flow-step-box">
    <div class="flow-step-header">
      <span>PASO 3: Dispatcher Paralelo Desacoplado ⚡</span>
      <span class="badge-green">EJECUCIÓN CONCURRENTE</span>
    </div>
    <div class="flow-step-body">
      Obtenido un CUIT válido, el sistema dispara simultáneamente dos consultas independientes:
      <ul>
        <li><strong>Rama A (CUITOnline):</strong> Descarga de constancia oficial de AFIP/ARCA, categoría de IVA, razón social y condición de empleador. Persiste en <code>datos_json.cuitonline</code>.</li>
        <li><strong>Rama B (BCRA Central de Deudores):</strong> Consulta de situación de riesgo crediticio (Situación 1 a 5), saldo consolidado de deuda con entidades bancarias y registro de cheques rechazados. Persiste en <code>datos_json.bcra</code>.</li>
      </ul>
    </div>
  </div>

  <div class="flow-step-box">
    <div class="flow-step-header">
      <span>PASO 4: Consolidación Perfil 360°</span>
      <span class="badge-green">ESTADO COMPLETADO</span>
    </div>
    <div class="flow-step-body">
      El registro se guarda atómicamente en la base de datos con toda la información unificada por namespaces, listo para consumo por CRM o áreas de cobranza.
    </div>
  </div>

  <div class="page-break"></div>

  <!-- 6. CATALOGO EXTENDIDO GERENCIAL -->
  <h2><span class="section-tag">06</span> Catálogo de Scrapers: ¿Qué es, hacia dónde va y qué trae cada uno?</h2>
  <p>
    Ficha de detalle para cada uno de los <strong>13 motores del ecosistema</strong>, orientada a entender su rol operativo y el valor comercial de los datos rescatados:
  </p>

  <!-- 1. ENACOM WEB -->
  <div class="scraper-profile" style="border-left: 4px solid #0284c7;">
    <div class="scraper-header">
      <div class="scraper-title">1. ENACOM Web Adapter (Portabilidad Numérica Oficial en Vivo)</div>
      <span class="badge-orange">🚀 NUEVO MOTOR</span>
    </div>
    <div class="scraper-desc-grid">
      <div class="scraper-desc-label">¿Qué es?</div>
      <div class="scraper-desc-value">Motor en tiempo real que audita el portal oficial de numeración telefónica de la República Argentina.</div>
      <div class="scraper-desc-label">¿Hacia dónde va?</div>
      <div class="scraper-desc-value">Portal oficial en línea del Ente Nacional de Comunicaciones (<code>https://numeracion.enacom.gob.ar/</code>).</div>
      <div class="scraper-desc-label">Mecanismo:</div>
      <div class="scraper-desc-value">Supera el CAPTCHA visual en &lt;250ms con tecnología 100% local: aplica un filtro de visión por computadora en OpenCV para borrar líneas de interferencia y lee los caracteres con el motor OCR nativo de Windows (sin costos de APIs externas).</div>
      <div class="scraper-desc-label">¿Qué trae?</div>
      <div class="scraper-desc-value"><strong>Prestador original</strong> (compañía titular histórica del rango), <strong>Prestador actual</strong> (compañía activa en vivo) y el indicador de si la línea fue portada. Identifica **Claro, Movistar, Personal, Telecentro** y cooperativas.</div>
      <div class="scraper-desc-label">Valor de Negocio:</div>
      <div class="scraper-desc-value">Permite saber con 100% de certeza a qué compañía llamar o enviar ofertas, evitando campañas fallidas a operadores erróneos.</div>
    </div>
  </div>

  <!-- 2. TELECENTRO -->
  <div class="scraper-profile" style="border-left: 4px solid #ea580c;">
    <div class="scraper-header">
      <div class="scraper-title">2. Telecentro Adapter (Validación de Abonados Telecentro S.A.)</div>
      <span class="badge-orange">🚀 NUEVO MOTOR</span>
    </div>
    <div class="scraper-desc-grid">
      <div class="scraper-desc-label">¿Qué es?</div>
      <div class="scraper-desc-value">Scraper especializado en identificar y validar clientes y servicios del operador Telecentro S.A.</div>
      <div class="scraper-desc-label">¿Hacia dónde va?</div>
      <div class="scraper-desc-value">Pasarelas de cobranza homologadas y sistemas de recaudación de Telecentro S.A.</div>
      <div class="scraper-desc-label">¿Qué trae?</div>
      <div class="scraper-desc-value">Confirmación de cliente activo, número de cuenta/abonado, estado del servicio, importe de deuda pendiente, fecha de vencimiento y códigos de barra de factura.</div>
      <div class="scraper-desc-label">Valor de Negocio:</div>
      <div class="scraper-desc-value">Completa la cobertura total del mercado argentino, cubriendo a los 4 grandes operadores (Claro, Movistar, Personal y Telecentro).</div>
    </div>
  </div>

  <!-- 3. POWER CRM FTTH -->
  <div class="scraper-profile" style="border-left: 4px solid #16a34a;">
    <div class="scraper-header">
      <div class="scraper-title">3. Power CRM FTTH (Factibilidad de Fibra Óptica al Hogar)</div>
      <span class="badge-orange">🚀 NUEVO EN SEPTIEMBRE (64,997 resueltos)</span>
    </div>
    <div class="scraper-desc-grid">
      <div class="scraper-desc-label">¿Qué es?</div>
      <div class="scraper-desc-value">Auditor de factibilidad técnica y cobertura para la comercialización de internet por fibra óptica.</div>
      <div class="scraper-desc-label">¿Hacia dónde va?</div>
      <div class="scraper-desc-value">Servicio cartográfico catastral de Google Maps y sistema de topología de red de fibra óptica.</div>
      <div class="scraper-desc-label">¿Qué trae?</div>
      <div class="scraper-desc-value">Coordenadas geográficas exactas (latitud/longitud), caja de dispersión más cercana, distancia en metros, disponibilidad de puertos libres y estado de aptitud de venta.</div>
      <div class="scraper-desc-label">Valor de Negocio:</div>
      <div class="scraper-desc-value">Habilita a los equipos comerciales a vender planes de fibra óptica únicamente en domicilios con cobertura confirmada, eliminando ventas caídas por falta de infraestructura.</div>
    </div>
  </div>

  <!-- 4. IRIS HTTP -->
  <div class="scraper-profile">
    <div class="scraper-header">
      <div class="scraper-title">4. IRIS HTTP Adapter (Movistar Argentina BPM)</div>
      <span class="badge-blue">CUENTA CORPORATIVA NUEVA</span>
    </div>
    <div class="scraper-desc-grid">
      <div class="scraper-desc-label">¿Qué es?</div>
      <div class="scraper-desc-value">Motor de ultra-alta velocidad contra el sistema central de gestión operativa de Movistar Argentina.</div>
      <div class="scraper-desc-label">¿Hacia dónde va?</div>
      <div class="scraper-desc-value">Servidor central de Movistar BPM (<code>iris.tmoviles.com.ar</code>).</div>
      <div class="scraper-desc-label">¿Qué trae?</div>
      <div class="scraper-desc-value">Nombre y apellido del titular, DNI/CUIT, tipo de plan, ciclo de facturación y el historial cronológico completo de movimientos: Altas, Bajas, Cambios de Titularidad, Port-In y Port-Out.</div>
      <div class="scraper-desc-label">Valor de Negocio:</div>
      <div class="scraper-desc-value">Identifica fehacientemente quién es el dueño real de la línea telefónica y si la conservó o la migró a otra empresa.</div>
    </div>
  </div>

  <!-- 5. IRIS BROWSER -->
  <div class="scraper-profile">
    <div class="scraper-header">
      <div class="scraper-title">5. IRIS Browser Adapter (Contingencia Visual Playwright)</div>
      <span class="badge-neutral">CONTINGENCIA VISUAL</span>
    </div>
    <div class="scraper-desc-grid">
      <div class="scraper-desc-label">¿Qué es?</div>
      <div class="scraper-desc-value">Scraper de respaldo visual que emula a un operador humano utilizando un navegador web automatizado.</div>
      <div class="scraper-desc-label">¿Hacia dónde va?</div>
      <div class="scraper-desc-value">Mismo portal Movistar BPM cuando el servidor bloquea peticiones de red directas.</div>
      <div class="scraper-desc-label">Valor de Negocio:</div>
      <div class="scraper-desc-value">Garantiza continuidad operativa: si el método rápido sufre un cambio en la interfaz, este robot visual toma el relevo sin detener la operación.</div>
    </div>
  </div>

  <!-- 6. CLARO -->
  <div class="scraper-profile">
    <div class="scraper-header">
      <div class="scraper-title">6. Claro Argentina Adapter (Cobro Express)</div>
      <span class="badge-green">CORTOCIRCUITO ACTIVO</span>
    </div>
    <div class="scraper-desc-grid">
      <div class="scraper-desc-label">¿Qué es?</div>
      <div class="scraper-desc-value">Validador de titularidad y facturas de telefonía móvil de Claro Argentina.</div>
      <div class="scraper-desc-label">¿Hacia dónde va?</div>
      <div class="scraper-desc-value">API oficial de la red nacional Cobro Express (Modalidad Claro: Línea + DNI).</div>
      <div class="scraper-desc-label">¿Qué trae?</div>
      <div class="scraper-desc-value">Importe exacto de deuda, código de barra de 37 dígitos, DNI del titular, fechas de vencimiento de comprobantes y validación de línea activa.</div>
      <div class="scraper-desc-label">Valor de Negocio:</div>
      <div class="scraper-desc-value">Confirma si el usuario es cliente Claro actual y permite accionar gestiones de cobranza o retención comercial.</div>
    </div>
  </div>

  <!-- 7. PERSONAL -->
  <div class="scraper-profile">
    <div class="scraper-header">
      <div class="scraper-title">7. Personal / Telecom Adapter (Cobro Express)</div>
      <span class="badge-green">CORTOCIRCUITO ACTIVO</span>
    </div>
    <div class="scraper-desc-grid">
      <div class="scraper-desc-label">¿Qué es?</div>
      <div class="scraper-desc-value">Validador de líneas y servicios de Telecom Personal / Flow.</div>
      <div class="scraper-desc-label">¿Hacia dónde va?</div>
      <div class="scraper-desc-value">Pasarela Cobro Express (Modalidad Cobranza sin Factura CBF por número de línea).</div>
      <div class="scraper-desc-label">¿Qué trae?</div>
      <div class="scraper-desc-value">Deuda vigente, códigos de recaudación y confirmación de que la línea opera bajo la red de Telecom.</div>
      <div class="scraper-desc-label">Valor de Negocio:</div>
      <div class="scraper-desc-value">Detecta clientes de Personal aun cuando no se disponía previamente del DNI de la persona.</div>
    </div>
  </div>

  <!-- 8. MOVISTAR COBRO EXPRESS -->
  <div class="scraper-profile">
    <div class="scraper-header">
      <div class="scraper-title">8. Movistar Cobro Express Adapter</div>
      <span class="badge-neutral">CANAL SECUNDARIO</span>
    </div>
    <div class="scraper-desc-grid">
      <div class="scraper-desc-label">¿Qué es?</div>
      <div class="scraper-desc-value">Canal alternativo para validar deudas de Movistar sin consumir las credenciales del sistema central.</div>
      <div class="scraper-desc-label">¿Hacia dónde va?</div>
      <div class="scraper-desc-value">Pasarela Cobro Express por característica geográfica y número local.</div>
      <div class="scraper-desc-label">Valor de Negocio:</div>
      <div class="scraper-desc-value">Proporciona un camino secundario para validar facturas de líneas fijas y móviles de Movistar.</div>
    </div>
  </div>

  <!-- 9. TELCOS CASCADE -->
  <div class="scraper-profile">
    <div class="scraper-header">
      <div class="scraper-title">9. Telcos en Cascada (Orquestador Secuencial)</div>
      <span class="badge-blue">ORQUESTADOR DE PIPELINE</span>
    </div>
    <div class="scraper-desc-grid">
      <div class="scraper-desc-label">¿Qué es?</div>
      <div class="scraper-desc-value">Cerebro de coordinación que ejecuta secuencialmente: Claro ➔ Personal ➔ Movistar.</div>
      <div class="scraper-desc-label">Valor de Negocio:</div>
      <div class="scraper-desc-value">Automatiza la toma de decisiones técnicas, garantizando que cada registro se investigue al menor costo posible y en el menor tiempo.</div>
    </div>
  </div>

  <!-- 10. CUITONLINE -->
  <div class="scraper-profile">
    <div class="scraper-header">
      <div class="scraper-title">10. CuitOnline Adapter (Enriquecimiento Tributario ARCA / AFIP)</div>
      <span class="badge-blue">ENRIQUECEDOR FISCAL</span>
    </div>
    <div class="scraper-desc-grid">
      <div class="scraper-desc-label">¿Qué es?</div>
      <div class="scraper-desc-value">Enriquecedor que convierte documentos de identidad en perfiles tributarios completos.</div>
      <div class="scraper-desc-label">¿Hacia dónde va?</div>
      <div class="scraper-desc-value">Padrón fiscal y constancias públicas de AFIP / ARCA.</div>
      <div class="scraper-desc-label">¿Qué trae?</div>
      <div class="scraper-desc-value">CUIT/CUIL oficial con dígito verificador corregido, Razón Social o Nombre fiscal, Tipo de Persona (Física o Jurídica), condición frente al IVA (Monotributo / Responsable Inscripto) y constancia de inscripción.</div>
      <div class="scraper-desc-label">Valor de Negocio:</div>
      <div class="scraper-desc-value">Permite la emisión de facturas legales, precalificación crediticia y verificación de solvencia fiscal.</div>
    </div>
  </div>

  <!-- 11. BCRA -->
  <div class="scraper-profile">
    <div class="scraper-header">
      <div class="scraper-title">11. BCRA Central de Deudores (Riesgo Crediticio)</div>
      <span class="badge-neutral">SISTEMA FINANCIERO</span>
    </div>
    <div class="scraper-desc-grid">
      <div class="scraper-desc-label">¿Qué es?</div>
      <div class="scraper-desc-value">Consultor de riesgo crediticio y comportamiento financiero bancario.</div>
      <div class="scraper-desc-label">¿Hacia dónde va?</div>
      <div class="scraper-desc-value">Central de Deudores del Banco Central de la República Argentina (BCRA).</div>
      <div class="scraper-desc-label">¿Qué trae?</div>
      <div class="scraper-desc-value">Calificación de riesgo (Situación 1: Normal hasta 5: Irrecuperable), entidades bancarias acreedoras, monto de deuda consolidada y cheques rechazados sin fondos.</div>
      <div class="scraper-desc-label">Valor de Negocio:</div>
      <div class="scraper-desc-value">Fundamental para evaluar la capacidad de pago y riesgo antes de otorgar productos financieros o planes pospago.</div>
    </div>
  </div>

  <!-- 12. DATUAR -->
  <div class="scraper-profile">
    <div class="scraper-header">
      <div class="scraper-title">12. Datuar Adapter (Padrón Telefónico y Personas)</div>
      <span class="badge-neutral">VÍNCULOS Y CONTACTOS</span>
    </div>
    <div class="scraper-desc-grid">
      <div class="scraper-desc-label">¿Qué es?</div>
      <div class="scraper-desc-value">Motor de búsqueda de relaciones personales y domicilios en bases de datos consolidadas.</div>
      <div class="scraper-desc-label">¿Qué trae?</div>
      <div class="scraper-desc-value">Teléfonos alternativos de contacto (fijos y móviles), domicilios postales históricos y vínculos de parentesco.</div>
      <div class="scraper-desc-label">Valor de Negocio:</div>
      <div class="scraper-desc-value">Multiplica las vías de contacto cuando un número primario no responde o fue dado de baja.</div>
    </div>
  </div>

  <!-- 13. ENACOM BLOQUES -->
  <div class="scraper-profile">
    <div class="scraper-header">
      <div class="scraper-title">13. ENACOM Bloques (Asignación Offline en Microsegundos)</div>
      <span class="badge-neutral">INSTANTÁNEO (&lt;1 ms)</span>
    </div>
    <div class="scraper-desc-grid">
      <div class="scraper-desc-label">¿Qué es?</div>
      <div class="scraper-desc-value">Base de datos en memoria del Plan Fundamental de Numeración de Argentina.</div>
      <div class="scraper-desc-label">¿Qué trae?</div>
      <div class="scraper-desc-value">Operador de origen asignado, provincia, localidad geográfica, modalidad (celular o fijo) y formato internacional E.164.</div>
      <div class="scraper-desc-label">Valor de Negocio:</div>
      <div class="scraper-desc-value">Permite normalizar y geolocalizar números al instante sin consumir internet ni generar costos de consulta.</div>
    </div>
  </div>

  <div class="page-break"></div>

  <!-- 7. TIEMPOS Y RENDIMIENTO -->
  <h2><span class="section-tag">07</span> Tiempos de Respuesta Promedio por Registro</h2>
  <table>
    <thead>
      <tr>
        <th>Scraper / Motor</th>
        <th>Tecnología / Protocolo</th>
        <th class="text-center">Promedio</th>
        <th class="text-center">Rango Típico</th>
        <th>Estado / Novedad</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td><strong>1. ENACOM Web Adapter</strong></td>
        <td>Playwright + OpenCV + WinOCR local</td>
        <td class="text-center"><strong>~1.8 s</strong></td>
        <td class="text-center">1.2s – 3.0s</td>
        <td><span class="badge-orange">🚀 NUEVO (Portabilidad en vivo)</span></td>
      </tr>
      <tr>
        <td><strong>2. Telecentro Adapter</strong></td>
        <td>Gateway de Recaudación / API</td>
        <td class="text-center"><strong>~1.5 s</strong></td>
        <td class="text-center">0.9s – 2.6s</td>
        <td><span class="badge-orange">🚀 NUEVO (Abonados Cable/Telco)</span></td>
      </tr>
      <tr>
        <td><strong>3. Power CRM FTTH</strong></td>
        <td>Geocoding + Polígonos de Fibra</td>
        <td class="text-center"><strong>~1.6 s</strong></td>
        <td class="text-center">0.9s – 3.1s</td>
        <td><span class="badge-orange">🚀 NUEVO en Septiembre</span></td>
      </tr>
      <tr>
        <td><strong>4. IRIS HTTP Adapter</strong></td>
        <td>HTTP directo JSF ViewState</td>
        <td class="text-center"><strong>~2.8 s</strong></td>
        <td class="text-center">0.72s – 18.2s</td>
        <td>Cuenta corporativa nueva</td>
      </tr>
      <tr>
        <td><strong>5. IRIS Browser Adapter</strong></td>
        <td>Playwright Chromium Headless</td>
        <td class="text-center"><strong>~18.5 s</strong></td>
        <td class="text-center">12.0s – 25.0s</td>
        <td>Bypass visual contingente</td>
      </tr>
      <tr>
        <td><strong>6. Claro (Cobro Express)</strong></td>
        <td>REST API Cobro Express</td>
        <td class="text-center"><strong>~1.4 s</strong></td>
        <td class="text-center">0.8s – 2.5s</td>
        <td>Modo Fast con Proxy Pool / Tor</td>
      </tr>
      <tr>
        <td><strong>7. Movistar (Cobro Express)</strong></td>
        <td>REST API Cobro Express</td>
        <td class="text-center"><strong>~1.5 s</strong></td>
        <td class="text-center">0.8s – 2.5s</td>
        <td>Consulta complementaria</td>
      </tr>
      <tr>
        <td><strong>8. Personal (Cobro Express)</strong></td>
        <td>REST API Cobro Express</td>
        <td class="text-center"><strong>~1.7 s</strong></td>
        <td class="text-center">0.9s – 2.8s</td>
        <td>Modalidad CBF sin factura</td>
      </tr>
      <tr>
        <td><strong>9. Telcos en Cascada</strong></td>
        <td>Claro ➔ Personal ➔ Movistar</td>
        <td class="text-center"><strong>~2.6 s</strong></td>
        <td class="text-center">1.2s – 5.8s</td>
        <td>Cascada con cortocircuito</td>
      </tr>
      <tr>
        <td><strong>10. CuitOnline Adapter</strong></td>
        <td>Web Scraping rotativo</td>
        <td class="text-center"><strong>~1.2 s</strong></td>
        <td class="text-center">0.8s – 1.8s</td>
        <td>Consulta fiscal DNI / CUIT</td>
      </tr>
      <tr>
        <td><strong>11. BCRA Central Deudores</strong></td>
        <td>API BCRA Deudores</td>
        <td class="text-center"><strong>~2.1 s</strong></td>
        <td class="text-center">1.5s – 3.2s</td>
        <td>Calificación de riesgo 1-5</td>
      </tr>
      <tr>
        <td><strong>12. Datuar Adapter</strong></td>
        <td>Web Scraping estructurado</td>
        <td class="text-center"><strong>~3.2 s</strong></td>
        <td class="text-center">2.0s – 4.5s</td>
        <td>Padrón telefónico nacional</td>
      </tr>
      <tr>
        <td><strong>13. ENACOM Bloques</strong></td>
        <td>Búsqueda binaria en RAM</td>
        <td class="text-center"><strong>&lt; 0.001 s</strong></td>
        <td class="text-center">0.1ms – 0.5ms</td>
        <td>Offline determinista</td>
      </tr>
    </tbody>
  </table>

  <!-- 8. RUTINAS Y GOBERNANZA -->
  <h2><span class="section-tag">08</span> Rutinas Operativas y Gobernanza de Infraestructura</h2>
  <ul>
    <li><strong>Rotación Preventiva Anti-Fugas de Memoria (Cada 350 consultas):</strong> Los procesos de scraping llevan un cómputo estricto de ejecuciones y se reinician de manera limpia al alcanzar 350 consultas. Esto previene la degradación de memoria RAM en servidores y PCs de trabajo.</li>
    <li><strong>Procesamiento en Lotes Seguros de 50 registros:</strong> Cada tanda de trabajo se reserva mediante bloqueos transaccionales a nivel base de datos (<code>FOR UPDATE SKIP LOCKED</code>), impidiendo que dos trabajadores o máquinas procesen accidentalmente el mismo número telefónico.</li>
    <li><strong>Política de Horarios de Disponibilidad Externa:</strong> El sistema respeta rigurosamente los horarios de atención de las plataformas consultadas para evitar alertas de seguridad:
      <ul>
        <li><strong>Movistar BPM:</strong> Lunes a Viernes de 08:00 a 21:00 hs | Sábados de 08:00 a 13:00 hs.</li>
        <li><strong>Redes de Cobranza (Cobro Express):</strong> Lunes a Viernes de 08:00 a 22:00 hs | Sábados de 08:00 a 13:00 hs.</li>
      </ul>
    </li>
    <li><strong>Staging Local Offline-First y Push Nocturno:</strong> Durante las horas pico diurnas, cada máquina almacena los resultados localmente en disco de alta velocidad. De 00:00 a 08:00 hs, un coordinador distribuido envía los datos masivamente al servidor central para proteger los canales de red y la base de datos principal.</li>
    <li><strong>Gestión Corporativa de Cuentas:</strong> Se integró una cuenta corporativa de alta disponibilidad para Registro No Llame, administrada junto a un pool distribuido de cuentas secundarias que rotan preventivamente para asegurar tolerancia a fallos.</li>
  </ul>

  <div class="footer-note">
    Documento confidencial elaborado para Dirección y Gerencia de Operaciones — CofradiaGS Ecosistema de Scraping
  </div>

</body>
</html>
"""

def main():
    pdf_path = BASE_DIR / "INFORME_SCRAPING_SEPTIEMBRE_2026.pdf"
    html_path = BASE_DIR / "INFORME_SCRAPING_SEPTIEMBRE_2026.html"

    # Guardar HTML completo sin imágenes
    html_path.write_text(HTML_CONTENT, encoding="utf-8")
    print(f"HTML generado en: {html_path}")

    # Renderizar PDF con Playwright
    print("Iniciando renderizado de PDF sin imágenes...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(HTML_CONTENT, wait_until="load")
        page.pdf(
            path=str(pdf_path),
            format="A4",
            print_background=True,
            margin={
                "top": "12mm",
                "bottom": "12mm",
                "left": "14mm",
                "right": "14mm"
            }
        )
        browser.close()

    print(f"PDF generado con éxito en: {pdf_path}")
    print(f"Tamaño del PDF: {pdf_path.stat().st_size:,} bytes")

if __name__ == "__main__":
    main()
