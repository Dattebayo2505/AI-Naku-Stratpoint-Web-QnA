"""Generate the official Stratpoint Services & License Pricing Handbook PDF in clean monochrome/slate corporate style."""

import os
from pathlib import Path
from playwright.sync_api import sync_playwright

HTML_CONTENT = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>
  @page {
    size: A4 portrait;
    margin: 12mm 14mm 14mm 14mm;
  }

  * {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
  }

  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    color: #1e293b;
    font-size: 8pt;
    line-height: 1.4;
    background: #ffffff;
  }

  .page {
    page-break-after: always;
    height: 100%;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    position: relative;
  }

  .page:last-child {
    page-break-after: avoid;
  }

  /* Document Header (Page 1) */
  .doc-header {
    border-bottom: 2px solid #0f172a;
    padding-bottom: 12px;
    margin-bottom: 14px;
  }

  .doc-eyebrow {
    font-size: 7pt;
    font-weight: 700;
    letter-spacing: 1px;
    text-transform: uppercase;
    color: #475569;
    margin-bottom: 4px;
  }

  .doc-title {
    font-size: 16pt;
    font-weight: 800;
    color: #0f172a;
    letter-spacing: -0.3px;
    margin-bottom: 5px;
  }

  .doc-subtitle {
    font-size: 8pt;
    color: #475569;
    margin-bottom: 10px;
    line-height: 1.35;
  }

  .doc-meta-bar {
    display: flex;
    flex-wrap: wrap;
    gap: 16px;
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 4px;
    padding: 6px 10px;
    font-size: 7pt;
    color: #334155;
  }

  .doc-meta-bar strong {
    color: #0f172a;
  }

  /* Running Page Headers (Pages 2+) */
  .page-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 1px solid #0f172a;
    padding-bottom: 4px;
    margin-bottom: 12px;
    font-size: 7pt;
    font-weight: 700;
    color: #0f172a;
    letter-spacing: 0.5px;
    text-transform: uppercase;
  }

  .page-header .right-title {
    color: #64748b;
    font-weight: 500;
  }

  /* Section Headings */
  .section-heading {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    border-bottom: 1px solid #cbd5e1;
    padding-bottom: 3px;
    margin-top: 10px;
    margin-bottom: 8px;
  }

  .section-title {
    font-size: 9pt;
    font-weight: 800;
    color: #0f172a;
    text-transform: uppercase;
    letter-spacing: 0.4px;
  }

  .section-source {
    font-size: 6.8pt;
    font-style: italic;
    color: #64748b;
  }

  .subsection-title {
    font-size: 8pt;
    font-weight: 700;
    color: #1e293b;
    margin: 8px 0 5px 0;
  }

  /* Stat / Overview Cards (Page 1) */
  .cards-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 6px;
    margin-bottom: 10px;
  }

  .stat-card {
    border: 1px solid #cbd5e1;
    background: #f8fafc;
    border-radius: 4px;
    padding: 7px 9px;
  }

  .stat-card-label {
    font-size: 6.5pt;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: #475569;
    margin-bottom: 4px;
  }

  .stat-card-rate {
    font-size: 8.5pt;
    font-weight: 800;
    color: #0f172a;
    margin-bottom: 2px;
  }

  .stat-card-sub {
    font-size: 6.5pt;
    color: #64748b;
    line-height: 1.3;
  }

  /* Monochrome Badges / Labels */
  .tag {
    display: inline-block;
    font-size: 6pt;
    font-weight: 700;
    padding: 1px 5px;
    border-radius: 3px;
    border: 1px solid #cbd5e1;
    background: #ffffff;
    color: #334155;
    text-transform: uppercase;
    letter-spacing: 0.3px;
  }

  /* Tables */
  table {
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 8px;
    font-size: 7.2pt;
    border: 1px solid #cbd5e1;
  }

  thead th {
    background: #f1f5f9;
    color: #0f172a;
    font-size: 6.8pt;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.4px;
    padding: 5px 7px;
    border-bottom: 1px solid #94a3b8;
    border-right: 1px solid #e2e8f0;
    text-align: left;
  }

  thead th:last-child {
    border-right: none;
  }

  tbody td {
    padding: 4.5px 7px;
    border-bottom: 1px solid #e2e8f0;
    border-right: 1px solid #f1f5f9;
    vertical-align: middle;
    color: #1e293b;
  }

  tbody tr:last-child td {
    border-bottom: none;
  }

  tbody td:last-child {
    border-right: none;
  }

  tbody tr:nth-child(even) {
    background: #f8fafc;
  }

  .rate-primary {
    font-weight: 700;
    color: #0f172a;
  }

  .rate-secondary {
    font-size: 6.5pt;
    color: #64748b;
  }

  .role-title {
    font-weight: 600;
    color: #0f172a;
  }

  .role-note {
    font-size: 6.5pt;
    color: #64748b;
  }

  /* Info / Callout Cards */
  .info-panel {
    border: 1px solid #cbd5e1;
    background: #f8fafc;
    border-radius: 4px;
    padding: 8px 12px;
    margin-bottom: 10px;
    margin-top: 4px;
  }

  .info-panel-title {
    font-size: 7.5pt;
    font-weight: 800;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: #0f172a;
    margin-bottom: 6px;
    border-bottom: 1px solid #e2e8f0;
    padding-bottom: 3px;
  }

  .info-panel-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 5px 18px;
    font-size: 7pt;
    color: #334155;
  }

  .info-panel-item {
    line-height: 1.35;
  }

  .info-panel-item strong {
    color: #0f172a;
  }

  /* Footer */
  .page-footer {
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-top: 1px solid #cbd5e1;
    padding-top: 5px;
    margin-top: 8px;
    font-size: 6.8pt;
    color: #64748b;
  }
</style>
</head>
<body>

<!-- ================= PAGE 1 ================= -->
<div class="page">
  <div>
    <!-- Document Header -->
    <div class="doc-header">
      <div class="doc-eyebrow">Official Rate Card &amp; Licensing Guide</div>
      <div class="doc-title">Stratpoint Services &amp; License Pricing Handbook</div>
      <div class="doc-subtitle">
        Official benchmark guide for engineering talent billing rates, workspace licenses, cloud infrastructure, quality assurance, data engineering, and enterprise AI software.
      </div>
      <div class="doc-meta-bar">
        <div><strong>Currency:</strong> Philippine Peso (PHP ₱)</div>
        <div><strong>Billing Modes:</strong> Hourly (T&amp;M), Monthly Retainer (160 hrs), Project-Based</div>
        <div><strong>Domains:</strong> Software, QA, Cloud, Data, AI</div>
      </div>
    </div>

    <!-- Section 1 -->
    <div class="section-heading">
      <div class="section-title">1. Software Services — Engineering Rates</div>
      <div class="section-source">Source: Philippines Software Developer Rates</div>
    </div>

    <!-- 1.1 Overview Cards -->
    <div class="subsection-title">1.1 Developer Experience Level Overview</div>
    <div class="cards-grid">
      <div class="stat-card">
        <div class="stat-card-label">Junior (0–2 yrs)</div>
        <div class="stat-card-rate">₱730.80 – ₱1,015.00/hr</div>
        <div class="stat-card-sub">Monthly: ₱116,928 – ₱181,888<br>Range: up to ₱1,136.80/hr</div>
      </div>
      <div class="stat-card">
        <div class="stat-card-label">Mid-Level (3–5 yrs)</div>
        <div class="stat-card-rate">₱1,136.80 – ₱1,705.20/hr</div>
        <div class="stat-card-sub">Monthly: ₱181,888 – ₱272,832</div>
      </div>
      <div class="stat-card">
        <div class="stat-card-label">Senior (5+ yrs)</div>
        <div class="stat-card-rate">₱1,624.00 – ₱2,639.00/hr</div>
        <div class="stat-card-sub">Monthly: ₱259,840 – ₱376,768+</div>
      </div>
      <div class="stat-card">
        <div class="stat-card-label">Architects</div>
        <div class="stat-card-rate">₱2,639.00+/hr</div>
        <div class="stat-card-sub">Enterprise System Architecture</div>
      </div>
    </div>

    <!-- Role & Seniority Table -->
    <div class="subsection-title">Developer Rates by Role &amp; Seniority</div>
    <table>
      <thead>
        <tr>
          <th style="width: 28%;">Role / Specialization</th>
          <th style="width: 24%;">Junior</th>
          <th style="width: 24%;">Mid-Level</th>
          <th style="width: 24%;">Senior / Lead</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td><div class="role-title">Frontend Developer</div></td>
          <td><span class="rate-primary">₱730.80 – ₱974.40/hr</span><br><span class="rate-secondary">₱116,928.00 – ₱155,904.00/mo</span></td>
          <td><span class="rate-primary">₱1,136.80 – ₱1,542.80/hr</span><br><span class="rate-secondary">₱181,888.00 – ₱246,848.00/mo</span></td>
          <td><span class="rate-primary">₱1,624.00 – ₱2,111.20/hr</span><br><span class="rate-secondary">₱259,840.00 – ₱337,792.00/mo</span></td>
        </tr>
        <tr>
          <td><div class="role-title">Backend Developer</div></td>
          <td><span class="rate-primary">₱812.00 – ₱1,055.60/hr</span><br><span class="rate-secondary">₱129,920.00 – ₱168,896.00/mo</span></td>
          <td><span class="rate-primary">₱1,218.00 – ₱1,624.00/hr</span><br><span class="rate-secondary">₱194,880.00 – ₱259,840.00/mo</span></td>
          <td><span class="rate-primary">₱1,705.20 – ₱2,233.00/hr</span><br><span class="rate-secondary">₱272,832.00 – ₱357,280.00/mo</span></td>
        </tr>
        <tr>
          <td><div class="role-title">Full-Stack Developer</div><div class="role-note">Overall: ₱1,421.00 – ₱2,233.00/hr</div></td>
          <td><span class="rate-primary">₱893.20 – ₱1,136.80/hr</span><br><span class="rate-secondary">₱142,912.00 – ₱181,888.00/mo</span></td>
          <td><span class="rate-primary">₱1,299.20 – ₱1,705.20/hr</span><br><span class="rate-secondary">₱207,872.00 – ₱272,832.00/mo</span></td>
          <td><span class="rate-primary">₱1,827.00 – ₱2,354.80/hr</span><br><span class="rate-secondary">₱292,320.00 – ₱376,768.00/mo</span></td>
        </tr>
        <tr>
          <td><div class="role-title">Mobile Developer</div><div class="role-note">Overall: ₱1,218.00 – ₱2,030.00/hr</div></td>
          <td><span class="rate-secondary">—</span></td>
          <td><span class="rate-primary">₱1,218.00 – ₱1,624.00/hr</span><br><span class="rate-secondary">₱194,880.00 – ₱259,840.00/mo</span></td>
          <td><span class="rate-primary">₱1,624.00 – ₱2,111.20/hr</span><br><span class="rate-secondary">₱259,840.00 – ₱337,792.00/mo</span></td>
        </tr>
        <tr>
          <td><div class="role-title">Mobile (iOS/Android Spec.)</div></td>
          <td><span class="rate-secondary">—</span></td>
          <td><span class="rate-primary">₱1,299.20 – ₱1,705.20/hr</span></td>
          <td>
            <span class="tag">Senior</span> <span class="rate-primary">₱1,827.00 – ₱2,354.80/hr</span><br>
            <span class="tag">Lead</span> <span class="rate-primary">₱2,354.80 – ₱2,923.20/hr</span>
          </td>
        </tr>
        <tr>
          <td><div class="role-title">Blockchain Developer</div></td>
          <td><span class="rate-secondary">—</span></td>
          <td><span class="rate-primary">₱1,705.20 – ₱2,233.00/hr</span></td>
          <td>
            <span class="tag">Senior</span> <span class="rate-primary">₱2,354.80 – ₱3,045.00/hr</span><br>
            <span class="tag">Lead</span> <span class="rate-primary">₱3,045.00 – ₱3,857.00/hr</span>
          </td>
        </tr>
        <tr>
          <td><div class="role-title">Security Engineer</div></td>
          <td><span class="rate-secondary">—</span></td>
          <td><span class="rate-primary">₱1,542.80 – ₱1,948.80/hr</span></td>
          <td>
            <span class="tag">Senior</span> <span class="rate-primary">₱2,111.20 – ₱2,760.80/hr</span><br>
            <span class="tag">Lead</span> <span class="rate-primary">₱2,760.80 – ₱3,451.00/hr</span>
          </td>
        </tr>
      </tbody>
    </table>

    <!-- Leadership Table -->
    <div class="subsection-title">Engineering Leadership &amp; Architecture Roles</div>
    <table>
      <thead>
        <tr>
          <th style="width: 35%;">Leadership Role</th>
          <th style="width: 32%;">Hourly Billing Rate</th>
          <th style="width: 33%;">Monthly Equivalent (160 hrs)</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td><div class="role-title">Technical Lead</div></td>
          <td><span class="rate-primary">₱2,233.00 – ₱2,760.80/hr</span></td>
          <td><span class="rate-primary">₱357,280.00 – ₱441,728.00/mo</span></td>
        </tr>
        <tr>
          <td><div class="role-title">Solution Architect</div></td>
          <td><span class="rate-primary">₱2,436.00 – ₱3,045.00/hr</span></td>
          <td><span class="rate-primary">₱389,760.00 – ₱487,200.00/mo</span></td>
        </tr>
        <tr>
          <td><div class="role-title">Engineering Manager</div></td>
          <td><span class="rate-primary">₱2,354.80 – ₱2,923.20/hr</span></td>
          <td><span class="rate-primary">₱376,768.00 – ₱467,712.00/mo</span></td>
        </tr>
        <tr>
          <td><div class="role-title">Principal Engineer</div></td>
          <td><span class="rate-primary">₱2,639.00 – ₱3,248.00+/hr</span></td>
          <td><span class="rate-primary">₱422,240.00 – ₱519,680.00+/mo</span></td>
        </tr>
      </tbody>
    </table>
  </div>

  <div class="page-footer">
    <div>Stratpoint Services &amp; License Pricing Handbook</div>
    <div>Page 1 of 3</div>
  </div>
</div>

<!-- ================= PAGE 2 ================= -->
<div class="page">
  <div>
    <div class="page-header">
      <div>Stratpoint Handbook • Sections 1 – 3</div>
      <div class="right-title">Software Services, QA &amp; Cloud Infrastructure</div>
    </div>

    <!-- Strategic Pricing Callout -->
    <div class="info-panel">
      <div class="info-panel-title">Strategic Pricing Models &amp; Effective Rates</div>
      <div class="info-panel-grid">
        <div class="info-panel-item">• <strong>Project-Based Pricing:</strong> 15–20% lower than Time &amp; Materials (T&amp;M).</div>
        <div class="info-panel-item">• <strong>True Cost (Junior Developer):</strong> ₱1,136.80 – ₱1,421.00/hr with 20–30% oversight &amp; rework.</div>
        <div class="info-panel-item">• <strong>Fully Loaded Rate (Mid-Level):</strong> ₱1,624.00/hr (₱1,218.00 base + 18% PM &amp; overhead).</div>
        <div class="info-panel-item">• <strong>Retainer Rate (Senior):</strong> ₱1,522.50/hr (₱243,600.00/mo for 160 hrs) vs ₱1,827.00–₱2,030.00 hourly.</div>
        <div class="info-panel-item" style="grid-column: span 2;">• <strong>Regional Variance:</strong> Metro Manila commands a 10–15% premium over Cebu City and Davao City.</div>
      </div>
    </div>

    <!-- Section 1.2 Google Workspace -->
    <div class="section-heading">
      <div class="section-title">1.2 Software &amp; Workspace Licenses — Google Workspace</div>
      <div class="section-source">Software &amp; Licenses PDF (Annual &amp; Monthly per user in PHP)</div>
    </div>

    <table>
      <thead>
        <tr>
          <th style="width: 32%;">Edition</th>
          <th style="width: 18%;">Type</th>
          <th style="width: 25%;">Tier 1 (1–9,999 Licenses)</th>
          <th style="width: 25%;">Tier 2 (10,000–59,999 Licenses)</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td rowspan="3" style="font-weight:700;">Google Workspace Starter</td>
          <td><span class="tag">New</span></td>
          <td><span class="rate-primary">₱4,527.35/yr</span> <span class="rate-secondary">(₱377.28/mo)</span></td>
          <td><span class="rate-primary">₱4,092.48/yr</span> <span class="rate-secondary">(₱341.04/mo)</span></td>
        </tr>
        <tr>
          <td><span class="tag">Renewal</span></td>
          <td><span class="rate-primary">₱5,291.16/yr</span> <span class="rate-secondary">(₱440.93/mo)</span></td>
          <td><span class="rate-primary">₱4,897.20/yr</span> <span class="rate-secondary">(₱408.10/mo)</span></td>
        </tr>
        <tr>
          <td><span class="tag">Transfer</span></td>
          <td><span class="rate-primary">₱5,291.16/yr</span> <span class="rate-secondary">(₱440.93/mo)</span></td>
          <td><span class="rate-primary">₱5,009.76/yr</span> <span class="rate-secondary">(₱417.48/mo)</span></td>
        </tr>

        <tr>
          <td rowspan="3" style="font-weight:700;">Google Workspace Standard</td>
          <td><span class="tag">New</span></td>
          <td><span class="rate-primary">₱11,110.43/yr</span> <span class="rate-secondary">(₱925.87/mo)</span></td>
          <td><span class="rate-primary">₱10,043.29/yr</span> <span class="rate-secondary">(₱836.94/mo)</span></td>
        </tr>
        <tr>
          <td><span class="tag">Renewal</span></td>
          <td><span class="rate-primary">₱12,986.65/yr</span> <span class="rate-secondary">(₱1,082.22/mo)</span></td>
          <td><span class="rate-primary">₱12,019.56/yr</span> <span class="rate-secondary">(₱1,001.63/mo)</span></td>
        </tr>
        <tr>
          <td><span class="tag">Transfer</span></td>
          <td><span class="rate-primary">₱12,986.65/yr</span> <span class="rate-secondary">(₱1,082.22/mo)</span></td>
          <td><span class="rate-primary">₱12,295.84/yr</span> <span class="rate-secondary">(₱1,024.65/mo)</span></td>
        </tr>

        <tr>
          <td rowspan="3" style="font-weight:700;">Google Workspace Plus</td>
          <td><span class="tag">New</span></td>
          <td><span class="rate-primary">₱14,405.75/yr</span> <span class="rate-secondary">(₱1,200.48/mo)</span></td>
          <td><span class="rate-primary">₱13,022.18/yr</span> <span class="rate-secondary">(₱1,085.18/mo)</span></td>
        </tr>
        <tr>
          <td><span class="tag">Renewal</span></td>
          <td><span class="rate-primary">₱16,830.32/yr</span> <span class="rate-secondary">(₱1,402.53/mo)</span></td>
          <td><span class="rate-primary">₱15,577.04/yr</span> <span class="rate-secondary">(₱1,298.08/mo)</span></td>
        </tr>
        <tr>
          <td><span class="tag">Transfer</span></td>
          <td><span class="rate-primary">₱16,830.32/yr</span> <span class="rate-secondary">(₱1,402.53/mo)</span></td>
          <td><span class="rate-primary">₱15,935.14/yr</span> <span class="rate-secondary">(₱1,327.93/mo)</span></td>
        </tr>
      </tbody>
    </table>

    <!-- Sections 2 & 3 -->
    <div class="section-heading">
      <div class="section-title">2–3. Quality Assurance &amp; Cloud Infrastructure</div>
      <div class="section-source">Philippines Software Developer Rates &amp; Software Licenses PDF</div>
    </div>

    <div class="subsection-title">2. Quality Assurance (QA)</div>
    <table>
      <thead>
        <tr>
          <th style="width: 35%;">Role / Seniority</th>
          <th style="width: 32%;">Hourly Billing Rate</th>
          <th style="width: 33%;">Monthly Equivalent (160 hrs)</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td><div class="role-title">Junior QA Engineer</div></td>
          <td><span class="rate-primary">₱730.80 – ₱893.20/hr</span></td>
          <td><span class="rate-primary">₱116,928.00 – ₱142,912.00/mo</span></td>
        </tr>
      </tbody>
    </table>

    <div class="subsection-title">3. Cloud Infrastructure &amp; Storage Add-Ons</div>
    <table>
      <thead>
        <tr>
          <th style="width: 45%;">Role / Storage Plan</th>
          <th style="width: 27%;">Rate / Tier 1 (1–9,999)</th>
          <th style="width: 28%;">Monthly / Tier 2 (10,000–59,999)</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td><div class="role-title">Mid-Level DevOps Engineer</div></td>
          <td><span class="rate-primary">₱1,299.20 – ₱1,705.20/hr</span></td>
          <td><span class="rate-primary">₱207,872.00 – ₱272,832.00/mo</span></td>
        </tr>
        <tr>
          <td><div class="role-title">Senior DevOps / SRE</div></td>
          <td><span class="rate-primary">₱1,827.00 – ₱2,354.80/hr</span></td>
          <td><span class="rate-primary">₱292,320.00 – ₱376,768.00/mo</span></td>
        </tr>
        <tr>
          <td><div class="role-title">Workspace 10TB Additional Storage (Add-on)</div></td>
          <td><span class="rate-primary">₱93,035.63/user/yr</span></td>
          <td><span class="rate-primary">₱84,100.00/user/yr</span></td>
        </tr>
      </tbody>
    </table>
  </div>

  <div class="page-footer">
    <div>Stratpoint Services &amp; License Pricing Handbook</div>
    <div>Page 2 of 3</div>
  </div>
</div>

<!-- ================= PAGE 3 ================= -->
<div class="page">
  <div>
    <div class="page-header">
      <div>Stratpoint Handbook • Sections 4 &amp; 5</div>
      <div class="right-title">Data Engineering &amp; Artificial Intelligence</div>
    </div>

    <!-- Section 4 -->
    <div class="section-heading">
      <div class="section-title">4. Data Engineering &amp; Analytics</div>
      <div class="section-source">Source: Philippines Software Developer Rates</div>
    </div>

    <div class="subsection-title">4.1 Data Engineering &amp; Python Development Rates</div>
    <table>
      <thead>
        <tr>
          <th style="width: 34%;">Role / Specialization</th>
          <th style="width: 22%;">Junior</th>
          <th style="width: 22%;">Mid-Level</th>
          <th style="width: 22%;">Senior / Spec.</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td><div class="role-title">Python Developer (Data &amp; Backend)</div></td>
          <td><span class="rate-primary">₱893.20 – ₱1,136.80/hr</span></td>
          <td><span class="rate-primary">₱1,299.20 – ₱1,705.20/hr</span></td>
          <td><span class="rate-primary">₱1,827.00 – ₱2,354.80/hr</span></td>
        </tr>
        <tr>
          <td><div class="role-title">Data Science &amp; AI/ML Specialists</div></td>
          <td colspan="2" style="text-align:center;"><span class="tag">Consulting Range</span></td>
          <td><span class="rate-primary">₱2,030.00 – ₱3,045.00/hr</span></td>
        </tr>
      </tbody>
    </table>

    <!-- Section 5 -->
    <div class="section-heading" style="margin-top: 14px;">
      <div class="section-title">5. Artificial Intelligence (AI &amp; ML)</div>
      <div class="section-source">Engineering Rates</div>
    </div>

    <div class="subsection-title">5.1 AI / ML Engineering &amp; Specialist Rates</div>
    <table>
      <thead>
        <tr>
          <th style="width: 36%;">Role &amp; Seniority Level</th>
          <th style="width: 32%;">Hourly Rate Range</th>
          <th style="width: 32%;">Monthly Equivalent (160 hrs)</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td><div class="role-title">AI/ML Specialists (Overview Range)</div></td>
          <td><span class="rate-primary">₱2,030.00 – ₱3,045.00/hr</span></td>
          <td><span class="rate-secondary">—</span></td>
        </tr>
        <tr>
          <td><div class="role-title">Mid-Level AI/ML Engineer</div></td>
          <td><span class="rate-primary">₱1,624.00 – ₱2,111.20/hr</span></td>
          <td><span class="rate-primary">₱259,840.00 – ₱337,792.00/mo</span></td>
        </tr>
        <tr>
          <td><div class="role-title">Senior AI/ML Engineer</div></td>
          <td><span class="rate-primary">₱2,233.00 – ₱2,842.00/hr</span></td>
          <td><span class="rate-primary">₱357,280.00 – ₱454,720.00/mo</span></td>
        </tr>
        <tr>
          <td><div class="role-title">Lead / Architect AI/ML Engineer</div></td>
          <td><span class="rate-primary">₱2,842.00 – ₱3,654.00/hr</span></td>
          <td><span class="rate-primary">₱454,720.00 – ₱584,640.00/mo</span></td>
        </tr>
      </tbody>
    </table>

    <!-- Commercial Governance Callout -->
    <div class="info-panel" style="margin-top: 14px;">
      <div class="info-panel-title">Commercial Governance &amp; Engagement Guidelines</div>
      <div class="info-panel-grid">
        <div class="info-panel-item">• <strong>Standard Billing Base:</strong> Monthly billing is computed at 160 productive hours per engineer month.</div>
        <div class="info-panel-item">• <strong>Tier Thresholds:</strong> Tier 1 encompasses 1–9,999 licenses; Tier 2 volume discount applies at 10,000–59,999 licenses.</div>
        <div class="info-panel-item">• <strong>Currency &amp; Taxes:</strong> All rates are quoted in Philippine Peso (PHP ₱). Applicable taxes (e.g. VAT) apply per SOW.</div>
        <div class="info-panel-item">• <strong>Enterprise SLA &amp; Add-ons:</strong> Specialized cloud architecture and SLA custom terms subject to formal proposal.</div>
      </div>
    </div>
  </div>

  <div class="page-footer">
    <div>Stratpoint Services &amp; License Pricing Handbook • Reference Document</div>
    <div>Page 3 of 3</div>
  </div>
</div>

</body>
</html>
"""

def generate_pdf():
    out_path = Path(__file__).resolve().parent.parent / "handbook.pdf"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(HTML_CONTENT, wait_until="load")
        page.emulate_media(media="print")
        page.pdf(
            path=str(out_path),
            format="A4",
            print_background=True,
            prefer_css_page_size=True,
            margin={"top": "0mm", "bottom": "0mm", "left": "0mm", "right": "0mm"}
        )
        browser.close()
    print(f"Generated {out_path} ({os.path.getsize(out_path)} bytes)")

if __name__ == "__main__":
    generate_pdf()
