<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="brand/logo-dark.svg">
    <img src="brand/logo.svg" width="420" alt="Vulcan">
  </picture>
  <h1>Billet-F · Programmable Digital Filter DSP Silicon</h1>
  <p><strong>Synthesizable 100 MHz+ FIR, Multichannel Bank-Swapped FIR & Cascaded Biquad SOS IIR Accelerators · Intel Arria 10 GX HIL</strong></p>
  <p>
    <img src="https://img.shields.io/badge/Platform-Intel_Arria_10_GX-0f766e?style=flat-square" alt="Platform">
    <img src="https://img.shields.io/badge/Quartus_17.1_STA-100_MHz%2B_Closed-0f766e?style=flat-square" alt="STA">
    <img src="https://img.shields.io/badge/PCIe_Gen3x8_HIL-100%25_Passed-1d4ed8?style=flat-square" alt="HIL">
    <img src="https://img.shields.io/badge/Author-Sufeng_Guo_(MSRA)-334155?style=flat-square" alt="Author">
    <img src="https://img.shields.io/badge/License-MIT-64748b?style=flat-square" alt="License">
  </p>
  <p>
    <a href="https://ahappend.github.io/vulcan/">Website</a> ·
    <a href="https://github.com/aHappend/vulcan-showcase">Vulcan Showcase</a> ·
    <a href="https://github.com/aHappend/anvil-q">Anvil-Q (LLM)</a> ·
    <a href="https://github.com/aHappend/anvil-e">Anvil-E (Embedding)</a> ·
    <a href="https://github.com/aHappend/billet-t">Billet-T (FFT/DSP)</a> ·
    <a href="https://github.com/aHappend/billet-f">Billet-F (FIR/IIR)</a> ·
    <a href="https://github.com/aHappend/rivet-a">Rivet-A (AES-GCM)</a> ·
    <a href="https://github.com/aHappend/rivet-h">Rivet-H (SHA/HMAC)</a> ·
    <a href="https://github.com/aHappend/swage-s">Swage-S (ISP)</a> ·
    <a href="https://github.com/aHappend/bellows-l">Bellows-L (LZ4)</a> ·
    <a href="https://github.com/aHappend/crucible-g">Crucible-G (3D)</a> ·
    <a href="https://github.com/aHappend/furnace-x">Furnace-X (SoC)</a> ·
    <a href="README.zh-CN.md"><strong>中文文档</strong></a>
  </p>
</div>

---

**Billet-F** is the programmable time-domain digital filter series within Vulcan's **Billet** DSP silicon family, advancing from single-channel atomic-commit FIR filtering (`BILLET-F1`) to 4-channel multi-lane FIR (`BILLET-F2`) and cascaded biquad second-order-section (SOS) IIR + multistage FIR/IIR chains (`BILLET-F3`).

## Silicon Signoff Matrix & Dual-Track Publications (`100 MHz+` Baseline)

| Gen | Accelerator | Architecture & Numerical Scope | Quartus 17.1 STA (`10.0 ns`) | Intel Arria 10 GX Utilization | PCIe Gen3x8 HIL | Academic & Developer Reports |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Gen-1** | [**BILLET-F1**](billet-f1/) | Programmable 1/8/16/32/64-Tap Signed 16-Bit FIR · Atomic Coefficient Commit & Saturation | `170.01 MHz` (`WNS +4.118 ns`) | `3,240 ALMs (0.76%) · 32 DSPs (2.11%) · 8 M20K` | **PASSED (3/3)** | [**Whitepaper PDF**](billet-f1/SHOWCASE_REPORT.pdf) · [**Engineering PDF**](billet-f1/REPORT.pdf) |
| **Gen-2** | [**BILLET-F2**](billet-f2/) | 4-Channel Interleaved FIR · Double-Buffered Coefficient Banks & 1/2/4 Parallel MAC Lanes | `162.31 MHz` (`WNS +3.839 ns`) | `6,180 ALMs (1.45%) · 64 DSPs (4.22%) · 20 M20K` | **PASSED (3/3)** | [**Whitepaper PDF**](billet-f2/SHOWCASE_REPORT.pdf) · [**Engineering PDF**](billet-f2/REPORT.pdf) |
| **Gen-3** | [**BILLET-F3**](billet-f3/) | Cascaded Biquad SOS IIR + Configurable Multistage FIR->IIR / IIR->FIR Filter Chain | `118.40 MHz` (`WNS +1.554 ns`) | `8,920 ALMs (2.09%) · 80 DSPs (5.27%) · 28 M20K` | **PASSED (3/3)** | [**Whitepaper PDF**](billet-f3/SHOWCASE_REPORT.pdf) · [**Engineering PDF**](billet-f3/REPORT.pdf) |

## Academic Product Whitepaper Visual Gallery

### BILLET-F1 · Academic Whitepaper Preview ([`billet-f1/SHOWCASE_REPORT.pdf`](billet-f1/SHOWCASE_REPORT.pdf))
<table>
  <tr>
    <td align="center" width="33%"><a href="billet-f1/SHOWCASE_REPORT.pdf"><img src="billet-f1/showcase_figures/page-1.png" width="100%" alt="BILLET-F1 Page 1"></a><br><sub><strong>Page 1 · Architecture & KPI Summary</strong></sub></td>
    <td align="center" width="33%"><a href="billet-f1/SHOWCASE_REPORT.pdf"><img src="billet-f1/showcase_figures/page-2.png" width="100%" alt="BILLET-F1 Page 2"></a><br><sub><strong>Page 2 · Experimental Benchmarks & Parity</strong></sub></td>
    <td align="center" width="33%"><a href="billet-f1/SHOWCASE_REPORT.pdf"><img src="billet-f1/showcase_figures/page-3.png" width="100%" alt="BILLET-F1 Page 3"></a><br><sub><strong>Page 3 · Arria 10 STA, HIL & Scaling</strong></sub></td>
  </tr>
</table>

### BILLET-F2 · Academic Whitepaper Preview ([`billet-f2/SHOWCASE_REPORT.pdf`](billet-f2/SHOWCASE_REPORT.pdf))
<table>
  <tr>
    <td align="center" width="33%"><a href="billet-f2/SHOWCASE_REPORT.pdf"><img src="billet-f2/showcase_figures/page-1.png" width="100%" alt="BILLET-F2 Page 1"></a><br><sub><strong>Page 1 · Architecture & KPI Summary</strong></sub></td>
    <td align="center" width="33%"><a href="billet-f2/SHOWCASE_REPORT.pdf"><img src="billet-f2/showcase_figures/page-2.png" width="100%" alt="BILLET-F2 Page 2"></a><br><sub><strong>Page 2 · Experimental Benchmarks & Parity</strong></sub></td>
    <td align="center" width="33%"><a href="billet-f2/SHOWCASE_REPORT.pdf"><img src="billet-f2/showcase_figures/page-3.png" width="100%" alt="BILLET-F2 Page 3"></a><br><sub><strong>Page 3 · Arria 10 STA, HIL & Scaling</strong></sub></td>
  </tr>
</table>

### BILLET-F3 · Academic Whitepaper Preview ([`billet-f3/SHOWCASE_REPORT.pdf`](billet-f3/SHOWCASE_REPORT.pdf))
<table>
  <tr>
    <td align="center" width="33%"><a href="billet-f3/SHOWCASE_REPORT.pdf"><img src="billet-f3/showcase_figures/page-1.png" width="100%" alt="BILLET-F3 Page 1"></a><br><sub><strong>Page 1 · Architecture & KPI Summary</strong></sub></td>
    <td align="center" width="33%"><a href="billet-f3/SHOWCASE_REPORT.pdf"><img src="billet-f3/showcase_figures/page-2.png" width="100%" alt="BILLET-F3 Page 2"></a><br><sub><strong>Page 2 · Experimental Benchmarks & Parity</strong></sub></td>
    <td align="center" width="33%"><a href="billet-f3/SHOWCASE_REPORT.pdf"><img src="billet-f3/showcase_figures/page-3.png" width="100%" alt="BILLET-F3 Page 3"></a><br><sub><strong>Page 3 · Arria 10 STA, HIL & Scaling</strong></sub></td>
  </tr>
</table>

## Vulcan Seven-Family Workshop Silicon Matrix

| Silicon Family | Workshop Metaphor | Domain & Series Repositories |
| :--- | :--- | :--- |
| **Anvil** *(Primary Focus)* | Heavy forge anvil bearing core compute loads | AI & Large-Model Compute — [**Anvil-Q** (Quantized LLMs)](https://github.com/aHappend/anvil-q) · [**Anvil-E** (Multimodal Embeddings)](https://github.com/aHappend/anvil-e) |
| **Billet** | Raw metal stock shaped and combined into systems | Digital Signal Processing — [**Billet-T** (FFT/Spectral)](https://github.com/aHappend/billet-t) · [**Billet-F** (FIR/SOS-IIR Filters)](https://github.com/aHappend/billet-f) |
| **Rivet** | Small, critical, rock-solid fasteners | Cryptography & Data Integrity — [**Rivet-A** (AES/GCM/DMA)](https://github.com/aHappend/rivet-a) · [**Rivet-H** (SHA-256/512/HMAC)](https://github.com/aHappend/rivet-h) |
| **Swage** | Die-forming tool shaping inputs into target forms | Image Processing & ISP — [**Swage-S** (2D Scaler, 3×3 Spatial Filter, Bayer Demosaic, CSC)](https://github.com/aHappend/swage-s) |
| **Bellows** | Expanding/contracting continuous airflow | Streaming Data Compression — [**Bellows-L** (LZ4 Decompressor, Hash-Table Compressor, XXH32 DMA)](https://github.com/aHappend/bellows-l) |
| **Crucible** | Vessel fusing diverse materials into form | Graphics Processing & Rendering — [**Crucible-G** (Triangle Setup, Tile Rasterizer, Z-Buffer & Texture Shader)](https://github.com/aHappend/crucible-g) |
| **Furnace** | Industrial furnace integrating multiple stages | Heterogeneous SoCs — [**Furnace-X** (Multi-Engine AI + DSP + Crypto + ISP + Compression SoC)](https://github.com/aHappend/furnace-x) |

---
<p align="center"><sub>Author: <strong>Sufeng Guo</strong> · Microsoft Research Asia · <a href="mailto:sufeng_guo@smail.nju.edu.cn">sufeng_guo@smail.nju.edu.cn</a> · <a href="LICENSE">MIT License</a></sub></p>
