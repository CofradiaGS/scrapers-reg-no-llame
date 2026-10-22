# -*- coding: utf-8 -*-
"""
Generador de Informe Ejecutivo en PDF para la base 'ce_datosmendoza'
Formato de alta fidelidad visual para analistas y directivos utilizando Playwright Chromium.
Matemáticamente consistente: todas las particiones suman exactamente 100.00%.
"""

from pathlib import Path
from playwright.sync_api import sync_playwright

HTML_CONTENT = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>Informe Ejecutivo - CE Datos Mendoza</title>
<style>
  @page {
    size: A4 portrait;
    margin: 10mm 12mm 10mm 12mm;
  }

  * {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
  }

  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    color: #1e293b;
    background-color: #ffffff;
    font-size: 8.8pt;
    line-height: 1.4;
  }

  /* Header */
  .header {
    border-bottom: 2.5px solid #1e3a8a;
    padding-bottom: 10px;
    margin-bottom: 14px;
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
  }

  .header-left h1 {
    font-size: 16pt;
    font-weight: 800;
    color: #0f172a;
    letter-spacing: -0.3px;
    text-transform: uppercase;
  }

  .header-left .subtitle {
    font-size: 9.5pt;
    color: #2563eb;
    font-weight: 700;
    margin-top: 2px;
  }

  .header-right {
    text-align: right;
    font-size: 7.8pt;
    color: #64748b;
  }

  .badge-tag {
    display: inline-block;
    background-color: #dbeafe;
    color: #1e40af;
    padding: 2px 7px;
    border-radius: 4px;
    font-weight: 700;
    font-size: 7.5pt;
    margin-bottom: 3px;
    text-transform: uppercase;
  }

  /* KPI Cards */
  .kpi-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 10px;
    margin-bottom: 14px;
  }

  .kpi-card {
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 6px;
    padding: 10px 12px;
    border-left: 4px solid #3b82f6;
  }

  .kpi-card.success { border-left-color: #10b981; background: #f0fdf4; }
  .kpi-card.warning { border-left-color: #f59e0b; background: #fffbeb; }
  .kpi-card.danger { border-left-color: #e11d48; background: #fff1f2; }

  .kpi-title {
    font-size: 7.5pt;
    text-transform: uppercase;
    font-weight: 700;
    color: #64748b;
    margin-bottom: 3px;
  }

  .kpi-value {
    font-size: 17pt;
    font-weight: 800;
    color: #0f172a;
    line-height: 1.1;
  }

  .kpi-desc {
    font-size: 7.5pt;
    color: #64748b;
    margin-top: 3px;
  }

  /* Progress Bar */
  .section-title {
    font-size: 9.5pt;
    font-weight: 800;
    color: #0f172a;
    margin-bottom: 8px;
    display: flex;
    align-items: center;
    gap: 6px;
    text-transform: uppercase;
    letter-spacing: 0.2px;
  }

  .bar-container {
    background: #f1f5f9;
    border-radius: 6px;
    height: 24px;
    display: flex;
    overflow: hidden;
    margin-bottom: 8px;
    border: 1px solid #cbd5e1;
    width: 100%;
  }

  .bar-claro { background: #e11d48; color: #ffffff; display: flex; align-items: center; justify-content: center; font-size: 7.5pt; font-weight: 700; }
  .bar-movistar { background: #0284c7; color: #ffffff; display: flex; align-items: center; justify-content: center; font-size: 7.5pt; font-weight: 700; }
  .bar-personal { background: #0d9488; color: #ffffff; display: flex; align-items: center; justify-content: center; font-size: 7.5pt; font-weight: 700; }
  .bar-sin { background: #94a3b8; color: #ffffff; display: flex; align-items: center; justify-content: center; font-size: 7.5pt; font-weight: 700; }

  .legend-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 8px;
    margin-bottom: 14px;
    font-size: 7.6pt;
    background: #f8fafc;
    padding: 7px 10px;
    border-radius: 6px;
    border: 1px solid #e2e8f0;
  }

  .legend-item {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .legend-dot {
    width: 10px;
    height: 10px;
    border-radius: 2px;
    flex-shrink: 0;
  }

  /* Two Column Summary */
  .two-col-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    margin-bottom: 14px;
  }

  /* Tables */
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 8.3pt;
  }

  th {
    background: #1e3a8a;
    color: #ffffff;
    font-weight: 700;
    text-align: left;
    padding: 6px 8px;
    font-size: 7.8pt;
    text-transform: uppercase;
    letter-spacing: 0.3px;
  }

  th.num, td.num {
    text-align: right;
  }

  th.center, td.center {
    text-align: center;
  }

  td {
    padding: 6px 8px;
    border-bottom: 1px solid #e2e8f0;
    color: #334155;
  }

  tr:nth-child(even) td {
    background-color: #f8fafc;
  }

  tr.total-row td {
    background-color: #f1f5f9;
    font-weight: 800;
    color: #0f172a;
    border-top: 2px solid #cbd5e1;
    border-bottom: 2px solid #cbd5e1;
  }

  .tag-op {
    display: inline-block;
    padding: 2px 6px;
    border-radius: 3px;
    font-weight: 700;
    font-size: 7.2pt;
    color: #ffffff;
  }
  .tag-claro { background-color: #e11d48; }
  .tag-movistar { background-color: #0284c7; }
  .tag-personal { background-color: #0d9488; }
  .tag-sin { background-color: #64748b; }
  .tag-pos { background-color: #10b981; }

  /* Insights Box */
  .insights-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 10px;
    margin-bottom: 12px;
  }

  .insight-box {
    background: #f8fafc;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 9px 11px;
  }

  .insight-box h3 {
    font-size: 8.3pt;
    font-weight: 800;
    color: #0f172a;
    margin-bottom: 4px;
    display: flex;
    align-items: center;
    gap: 5px;
  }

  .insight-box p {
    font-size: 7.6pt;
    color: #475569;
    line-height: 1.35;
  }

  /* Technical Details */
  .meta-banner {
    background: #f1f5f9;
    border: 1px dashed #94a3b8;
    border-radius: 6px;
    padding: 7px 12px;
    display: flex;
    justify-content: space-between;
    font-size: 7.4pt;
    color: #475569;
    margin-bottom: 10px;
  }

  /* Footer */
  .footer {
    border-top: 1px solid #cbd5e1;
    padding-top: 8px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 7.2pt;
    color: #64748b;
  }
</style>
</head>
<body>

  <!-- Header -->
  <div class="header">
    <div class="header-left">
      <div class="badge-tag">Módulo Analítico Telco &bull; Producción</div>
      <h1>Informe Ejecutivo: CE Datos Mendoza</h1>
      <div class="subtitle">Auditoría de Enriquecimiento y Match de Titularidad (Claro • Movistar • Personal)</div>
    </div>
    <div class="header-right">
      <div><strong>Base:</strong> scraper_cobro_express (fuente: datos_mendoza)</div>
      <div><strong>Cola Central:</strong> cola_automatizacion</div>
      <div><strong>Fecha de Ejecución:</strong> Octubre 2026</div>
      <div><strong>Estado:</strong> 100% Completado</div>
    </div>
  </div>

  <!-- KPI Cards -->
  <div class="kpi-grid">
    <div class="kpi-card">
      <div class="kpi-title">Padrón Total Analizado</div>
      <div class="kpi-value">23.291</div>
      <div class="kpi-desc">100% registros procesados</div>
    </div>
    <div class="kpi-card success">
      <div class="kpi-title">Coincidencias Positivas</div>
      <div class="kpi-value">14.838</div>
      <div class="kpi-desc"><strong>63.71%</strong> con titular confirmado</div>
    </div>
    <div class="kpi-card danger">
      <div class="kpi-title">Líder Regional</div>
      <div class="kpi-value">Claro (71.6%)</div>
      <div class="kpi-desc">10.625 líneas identificadas</div>
    </div>
    <div class="kpi-card warning">
      <div class="kpi-title">Sin Coincidencia</div>
      <div class="kpi-value">8.453</div>
      <div class="kpi-desc"><strong>36.29%</strong> no activas / prepagas</div>
    </div>
  </div>

  <!-- Visual Distribution Bar (Suma exactamente 100.00%) -->
  <div class="section-title">Distribución Visual del Padrón Total (Suma 100.00%)</div>
  <div class="bar-container">
    <div class="bar-claro" style="width: 45.62%;">Claro 45.62%</div>
    <div class="bar-movistar" style="width: 17.22%;">Movistar 17.22%</div>
    <div class="bar-personal" style="width: 0.87%;">P.</div>
    <div class="bar-sin" style="width: 36.29%;">Sin Coincidencia 36.29%</div>
  </div>

  <div class="legend-grid">
    <div class="legend-item">
      <div class="legend-dot" style="background: #e11d48;"></div>
      <span><strong>Claro:</strong> 10.625 (45.62%)</span>
    </div>
    <div class="legend-item">
      <div class="legend-dot" style="background: #0284c7;"></div>
      <span><strong>Movistar:</strong> 4.011 (17.22%)</span>
    </div>
    <div class="legend-item">
      <div class="legend-dot" style="background: #0d9488;"></div>
      <span><strong>Personal:</strong> 202 (0.87%)</span>
    </div>
    <div class="legend-item">
      <div class="legend-dot" style="background: #94a3b8;"></div>
      <span><strong>Sin Coincidencia:</strong> 8.453 (36.29%)</span>
    </div>
  </div>

  <!-- Two Tables for Absolute Mathematical Clarity -->
  <div class="two-col-grid">
    <!-- Tabla 1: Partición General -->
    <div>
      <div class="section-title">1. Partición General del Padrón</div>
      <table>
        <thead>
          <tr>
            <th>Categoría</th>
            <th class="num">Líneas</th>
            <th class="num">% Padrón</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><span class="tag-op tag-pos">CON COINCIDENCIA</span></td>
            <td class="num"><strong>14.838</strong></td>
            <td class="num"><strong>63.71%</strong></td>
          </tr>
          <tr>
            <td><span class="tag-op tag-sin">SIN COINCIDENCIA</span></td>
            <td class="num">8.453</td>
            <td class="num">36.29%</td>
          </tr>
          <tr class="total-row">
            <td>TOTAL PADRÓN MENDOZA</td>
            <td class="num">23.291</td>
            <td class="num">100.00%</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Tabla 2: Desglose de Aciertos por Operador -->
    <div>
      <div class="section-title">2. Desglose de Coincidencias por Empresa</div>
      <table>
        <thead>
          <tr>
            <th>Operador</th>
            <th class="num">Líneas</th>
            <th class="num">% s/ Aciertos</th>
            <th class="num">% Padrón</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><span class="tag-op tag-claro">CLARO</span></td>
            <td class="num">10.625</td>
            <td class="num">71.61%</td>
            <td class="num">45.62%</td>
          </tr>
          <tr>
            <td><span class="tag-op tag-movistar">MOVISTAR</span></td>
            <td class="num">4.011</td>
            <td class="num">27.03%</td>
            <td class="num">17.22%</td>
          </tr>
          <tr>
            <td><span class="tag-op tag-personal">PERSONAL</span></td>
            <td class="num">202</td>
            <td class="num">1.36%</td>
            <td class="num">0.87%</td>
          </tr>
          <tr class="total-row">
            <td>TOTAL ACIERTOS</td>
            <td class="num">14.838</td>
            <td class="num">100.00%</td>
            <td class="num">63.71%</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- Tabla Maestra Desglosada Unificada -->
  <div class="section-title">3. Tabla Maestra Completa (Suma Exacta 100.00%)</div>
  <table style="margin-bottom: 12px;">
    <thead>
      <tr>
        <th>Operador / Estado</th>
        <th class="num">Líneas</th>
        <th class="num">% Total Base</th>
        <th class="center">Posta Pipeline</th>
        <th>Estrategia de Salida</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td><span class="tag-op tag-claro">CLARO</span></td>
        <td class="num"><strong>10.625</strong></td>
        <td class="num">45.62%</td>
        <td class="center">Posta 1</td>
        <td>Cortocircuito Inmediato (Ahorro de consultas)</td>
      </tr>
      <tr>
        <td><span class="tag-op tag-movistar">MOVISTAR</span></td>
        <td class="num"><strong>4.011</strong></td>
        <td class="num">17.22%</td>
        <td class="center">Posta 3</td>
        <td>Match Positivo en Posta Final</td>
      </tr>
      <tr>
        <td><span class="tag-op tag-personal">PERSONAL</span></td>
        <td class="num"><strong>202</strong></td>
        <td class="num">0.87%</td>
        <td class="center">Posta 2</td>
        <td>Match Positivo Intermedio</td>
      </tr>
      <tr>
        <td><span class="tag-op tag-sin">SIN COINCIDENCIA</span></td>
        <td class="num"><strong>8.453</strong></td>
        <td class="num">36.29%</td>
        <td class="center">Cascada Completa</td>
        <td>Recorrió Claro, Personal y Movistar sin hallazgo</td>
      </tr>
      <tr class="total-row">
        <td>TOTAL PADRÓN MENDOZA</td>
        <td class="num">23.291</td>
        <td class="num">100.00%</td>
        <td class="center">—</td>
        <td>100% de la Base Procesada y Sincronizada</td>
      </tr>
    </tbody>
  </table>

  <!-- Insights -->
  <div class="section-title">Hallazgos Estratégicos & Eficiencia del Sistema</div>
  <div class="insights-grid">
    <div class="insight-box">
      <h3>📈 Dominio Regional Claro</h3>
      <p>Claro abarca más del <strong>71.6%</strong> de los hallazgos positivos en el padrón de Mendoza, consolidando una marcada primacía territorial respecto a Movistar (27.0%) y Personal (1.4%).</p>
    </div>
    <div class="insight-box">
      <h3>⚡ Eficiencia del Cortocircuito</h3>
      <p>Al resolverse 10.625 registros en la primera posta (Claro), el sistema aplicó *Short-Circuit*, <strong>ahorrando más de 21.000 peticiones de red</strong> a Personal y Movistar y acelerando el throughput.</p>
    </div>
    <div class="insight-box">
      <h3>🔒 Integridad de Datos en VPS</h3>
      <p>El 100% de los positivos cuenta con titularidad normalizada, DNI validado y persistencia atómica en <code>cola_automatizacion</code> con payload enriquecido para CRM y discadores.</p>
    </div>
  </div>

  <!-- Technical Meta -->
  <div class="meta-banner">
    <div><strong>Arquitectura:</strong> Hexagonal Desacoplada (Puertos & Adaptadores)</div>
    <div><strong>Motor de Scraping:</strong> Telco Cascade Bot (Tor Stream Isolation)</div>
    <div><strong>Persistencia:</strong> SQLite WAL Local &rarr; MySQL VPS Central</div>
  </div>

  <!-- Footer -->
  <div class="footer">
    <div>Sistema Industrial Scraper Telco &bull; Auditoría Oficial de Producción</div>
    <div>Documento generado de forma automatizada &bull; Archivo: <code>informe_ce_datosmendoza.pdf</code></div>
  </div>

</body>
</html>
"""

def generar_pdf():
    pdf_path = Path("informe_ce_datosmendoza.pdf").resolve()
    print(f"Generando PDF ejecutivo en: {pdf_path}...")
    
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(HTML_CONTENT)
        page.pdf(
            path=str(pdf_path),
            format="A4",
            print_background=True,
            margin={
                "top": "8mm",
                "bottom": "8mm",
                "left": "10mm",
                "right": "10mm"
            }
        )
        browser.close()
    
    print(f"PDF generado exitosamente ({pdf_path.stat().st_size:,} bytes).")

if __name__ == "__main__":
    generar_pdf()
