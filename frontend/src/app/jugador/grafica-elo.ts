import { Component, computed, input, signal } from '@angular/core';
import type { PuntoElo } from '../api/tenis-api.service';
import { formatearFecha } from '../formato';

const ANCHO = 900;
const ALTO = 300;
const M = { arriba: 24, derecha: 16, abajo: 30, izquierda: 52 };

interface Punto { x: number; y: number; fecha: string; elo: number; }

// Evolución del Elo partido a partido. Una sola serie: sin leyenda, el título la nombra.
// Cursor y tooltip al pasar el ratón; con el foco, las flechas recorren los partidos.
@Component({
  selector: 'app-grafica-elo',
  template: `
    <div class="marco">
      <svg [attr.viewBox]="'0 0 ' + ancho + ' ' + alto" role="img" tabindex="0"
           [attr.aria-label]="resumen()"
           (pointermove)="mover($event)" (pointerleave)="indice.set(-1)" (keydown)="tecla($event)" (blur)="indice.set(-1)">
        @for (t of marcasY(); track t.valor) {
          <line class="rejilla" [attr.x1]="m.izquierda" [attr.x2]="ancho - m.derecha" [attr.y1]="t.y" [attr.y2]="t.y" />
          <text class="eje" [attr.x]="m.izquierda - 8" [attr.y]="t.y + 4" text-anchor="end">{{ t.texto }}</text>
        }
        @for (t of marcasX(); track t.texto) {
          <text class="eje" [attr.x]="t.x" [attr.y]="alto - 8" text-anchor="middle">{{ t.texto }}</text>
        }
        <path class="linea" [attr.d]="trazo()" />

        @if (maximo(); as p) {
          <circle class="punto" [attr.cx]="p.x" [attr.cy]="p.y" r="4" />
          <text class="etiqueta" [attr.x]="p.x" [attr.y]="p.y - 10" [attr.text-anchor]="p.x > ancho * 0.8 ? 'end' : 'middle'">
            Máximo {{ p.elo.toLocaleString('es-ES', { maximumFractionDigits: 0 }) }}
          </text>
        }
        @if (activo(); as p) {
          <line class="cursor" [attr.x1]="p.x" [attr.x2]="p.x" [attr.y1]="m.arriba" [attr.y2]="alto - m.abajo" />
          <circle class="punto activo" [attr.cx]="p.x" [attr.cy]="p.y" r="5" />
        }
      </svg>
      @if (activo(); as p) {
        <div class="tooltip" [style.left.%]="(p.x / ancho) * 100" [style.top.%]="(p.y / alto) * 100"
             [class.izquierda]="p.x > ancho * 0.7">
          <strong class="cifras">{{ p.elo.toLocaleString('es-ES', { maximumFractionDigits: 0 }) }}</strong>
          <span>{{ fecha(p.fecha) }}</span>
        </div>
      }
    </div>
  `,
  styles: `
    .marco { position: relative; }
    svg { display: block; width: 100%; height: auto; touch-action: pan-y; }
    svg:focus-visible { outline: 2px solid var(--pista-texto); outline-offset: 4px; }
    .rejilla { stroke: var(--linea); stroke-width: 1; }
    .eje { fill: var(--gris); font-size: 13px; font-variant-numeric: tabular-nums; }
    .linea { fill: none; stroke: var(--pista-texto); stroke-width: 2; stroke-linejoin: round; }
    .punto { fill: var(--pista-texto); stroke: var(--fondo); stroke-width: 2; }
    .etiqueta { fill: var(--tinta); font-size: 13px; font-weight: 600; font-variant-numeric: tabular-nums; }
    .cursor { stroke: var(--gris); stroke-width: 1; stroke-dasharray: 3 3; }
    .tooltip {
      position: absolute; transform: translate(12px, -50%); pointer-events: none; white-space: nowrap;
      background: var(--papel); border: 1px solid var(--linea); border-radius: 4px; padding: 6px 10px;
      display: grid; font-size: 0.875rem; color: var(--gris);
    }
    .tooltip strong { color: var(--tinta); font-size: 1.05rem; }
    .tooltip.izquierda { transform: translate(calc(-100% - 12px), -50%); }
  `
})
export class GraficaElo {
  readonly puntos = input.required<PuntoElo[]>();

  protected readonly ancho = ANCHO;
  protected readonly alto = ALTO;
  protected readonly m = M;
  protected readonly fecha = formatearFecha;
  protected readonly indice = signal(-1);

  private readonly escalas = computed(() => {
    const ps = this.puntos();
    const tiempos = ps.map(p => Date.parse(p.fecha));
    const t0 = tiempos[0], t1 = Math.max(tiempos[tiempos.length - 1], t0 + 1);
    const elos = ps.map(p => p.elo);
    const paso = 100;
    const min = Math.floor(Math.min(...elos) / paso) * paso;
    const max = Math.ceil(Math.max(...elos) / paso) * paso;
    const x = (t: number) => M.izquierda + ((t - t0) / (t1 - t0)) * (ANCHO - M.izquierda - M.derecha);
    const y = (e: number) => ALTO - M.abajo - ((e - min) / Math.max(max - min, 1)) * (ALTO - M.arriba - M.abajo);
    return { x, y, min, max, t0, t1, tiempos };
  });

  private readonly coordenadas = computed<Punto[]>(() => {
    const { x, y, tiempos } = this.escalas();
    return this.puntos().map((p, i) => ({ x: x(tiempos[i]), y: y(p.elo), fecha: p.fecha, elo: p.elo }));
  });

  protected readonly trazo = computed(() =>
    this.coordenadas().map((p, i) => `${i ? 'L' : 'M'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(''));

  protected readonly marcasY = computed(() => {
    const { y, min, max } = this.escalas();
    const paso = Math.max(100, Math.ceil((max - min) / 4 / 100) * 100);
    const marcas = [];
    for (let v = min; v <= max; v += paso)
      marcas.push({ valor: v, y: y(v), texto: v.toLocaleString('es-ES') });
    return marcas;
  });

  protected readonly marcasX = computed(() => {
    const { x, t0, t1 } = this.escalas();
    const primero = new Date(t0).getFullYear() + 1, ultimo = new Date(t1).getFullYear();
    const salto = Math.max(1, Math.ceil((ultimo - primero + 1) / 8));
    const marcas = [];
    for (let a = primero; a <= ultimo; a += salto)
      marcas.push({ x: x(Date.UTC(a, 0, 1)), texto: String(a) });
    return marcas;
  });

  protected readonly maximo = computed(() =>
    this.coordenadas().reduce<Punto | null>((mejor, p) => (!mejor || p.elo > mejor.elo ? p : mejor), null));

  protected readonly activo = computed(() => this.coordenadas()[this.indice()] ?? null);

  protected readonly resumen = computed(() => {
    const ps = this.puntos();
    const max = this.maximo();
    if (!ps.length || !max) return 'Sin partidos';
    return `Evolución del Elo desde ${formatearFecha(ps[0].fecha)}: máximo de ${Math.round(max.elo)} el ${formatearFecha(max.fecha)}, ` +
      `${Math.round(ps[ps.length - 1].elo)} antes de su último partido.`;
  });

  protected mover(evento: PointerEvent): void {
    const svg = evento.currentTarget as SVGSVGElement;
    const caja = svg.getBoundingClientRect();
    const x = ((evento.clientX - caja.left) / caja.width) * ANCHO;
    const cs = this.coordenadas();
    // Búsqueda binaria del partido más cercano en el eje del tiempo
    let lo = 0, hi = cs.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (cs[mid].x < x) lo = mid + 1; else hi = mid;
    }
    if (lo > 0 && Math.abs(cs[lo - 1].x - x) < Math.abs(cs[lo].x - x)) lo--;
    this.indice.set(lo);
  }

  protected tecla(evento: KeyboardEvent): void {
    const total = this.puntos().length;
    if (evento.key === 'ArrowRight' || evento.key === 'ArrowLeft') {
      evento.preventDefault();
      const actual = this.indice() < 0 ? total - 1 : this.indice();
      this.indice.set(Math.min(total - 1, Math.max(0, actual + (evento.key === 'ArrowRight' ? 1 : -1))));
    }
  }
}
