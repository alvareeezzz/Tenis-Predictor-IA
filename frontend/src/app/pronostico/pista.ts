import { Component, computed, input } from '@angular/core';
import { formatearProbabilidad } from '../formato';

// Pista vista desde arriba (medidas reales: 23,77 × 10,97 m). La red del pronóstico se coloca
// donde cae la probabilidad: el campo de cada jugador mide lo que valen sus opciones.
// La red real, en el centro, queda marcada de fondo como referencia del 50 %.
const X0 = 20;
const LARGO = 960;
const Y0 = 20;
const ANCHO = 430;
const PASILLO = ANCHO * (1.37 / 10.97);
const SAQUE = (LARGO / 2) * (6.4 / 11.885);
const TAMANO_MAXIMO = 120;
const ANCHO_POR_PX = 1.9; // "88 %" en letra estrecha ocupa unas 1,9 veces su tamaño de letra

const CENTRO = X0 + LARGO / 2;

// Cada cifra va en la zona de fondo de su campo (entre la línea de fondo y la de saque), que no
// tiene líneas que la crucen. Si la red entra en esa zona, la cifra se queda entre el fondo y la red.
const cifraEnCampo = (desde: number, hasta: number) => ({
  x: (desde + hasta) / 2,
  tamano: Math.max(28, Math.min(TAMANO_MAXIMO, (hasta - desde - 36) / ANCHO_POR_PX))
});

@Component({
  selector: 'app-pista',
  template: `
    <svg viewBox="0 0 1000 470" role="img" [attr.aria-label]="descripcion()">
      <defs>
        <clipPath id="recorte-pista"><rect [attr.x]="x0" [attr.y]="y0" [attr.width]="largo" [attr.height]="ancho" /></clipPath>
      </defs>
      <rect class="suelo" [attr.x]="x0" [attr.y]="y0" [attr.width]="largo" [attr.height]="ancho" />

      <!-- Campo del jugador B, oscurecido, desde la red del pronóstico hasta su fondo -->
      <g clip-path="url(#recorte-pista)">
        <rect class="campo-b" [style.transform]="desplazamiento()" x="0" [attr.y]="y0" [attr.width]="largo" [attr.height]="ancho" />
      </g>

      <g class="lineas">
        <rect [attr.x]="x0" [attr.y]="y0" [attr.width]="largo" [attr.height]="ancho" />
        <line [attr.x1]="x0" [attr.x2]="x0 + largo" [attr.y1]="y0 + pasillo" [attr.y2]="y0 + pasillo" />
        <line [attr.x1]="x0" [attr.x2]="x0 + largo" [attr.y1]="y0 + ancho - pasillo" [attr.y2]="y0 + ancho - pasillo" />
        <line [attr.x1]="centro - saque" [attr.x2]="centro - saque" [attr.y1]="y0 + pasillo" [attr.y2]="y0 + ancho - pasillo" />
        <line [attr.x1]="centro + saque" [attr.x2]="centro + saque" [attr.y1]="y0 + pasillo" [attr.y2]="y0 + ancho - pasillo" />
        <line [attr.x1]="centro - saque" [attr.x2]="centro + saque" [attr.y1]="y0 + ancho / 2" [attr.y2]="y0 + ancho / 2" />
      </g>
      <line class="red-real" [attr.x1]="centro" [attr.x2]="centro" [attr.y1]="y0" [attr.y2]="y0 + ancho" />
      @if (verMitad()) {
        <text class="mitad" [class.campo-oscuro]="xRed() < centro" [attr.x]="centro" [attr.y]="y0 + pasillo / 2" text-anchor="middle" dominant-baseline="central">50 %</text>
      }

      <g class="red" [style.transform]="desplazamiento()">
        <line x1="0" x2="0" [attr.y1]="y0 - 12" [attr.y2]="y0 + ancho + 12" />
        <circle cx="0" [attr.cy]="y0 - 12" r="7" />
        <circle cx="0" [attr.cy]="y0 + ancho + 12" r="7" />
      </g>

      <text class="marcador cifra" text-anchor="middle" dominant-baseline="central" [attr.x]="cifraA().x" [attr.y]="y0 + ancho / 2"
            [style.font-size.px]="cifraA().tamano">{{ textoA() }}</text>
      <text class="marcador cifra campo-oscuro" text-anchor="middle" dominant-baseline="central" [attr.x]="cifraB().x" [attr.y]="y0 + ancho / 2"
            [style.font-size.px]="cifraB().tamano">{{ textoB() }}</text>
    </svg>
  `,
  styles: `
    :host { display: block; }
    svg { display: block; width: 100%; height: auto; }
    .suelo { fill: var(--pista); }
    .campo-b { fill: #000; opacity: 0.24; transition: transform 0.6s cubic-bezier(0.3, 0.7, 0.2, 1); }
    .lineas { fill: none; stroke: var(--linea-pista); stroke-width: 5; opacity: 0.85; }
    .red-real { stroke: var(--linea-pista); stroke-width: 3; stroke-dasharray: 6 9; opacity: 0.55; }
    .mitad { fill: var(--sobre-pista); font-size: 17px; font-weight: 600; paint-order: stroke; stroke: var(--pista); stroke-width: 10px; }
    .mitad.campo-oscuro { stroke: color-mix(in srgb, var(--pista), #000 24%); }
    @media (max-width: 520px) { .mitad { display: none; } } /* a ese ancho no se leería */
    .red { transition: transform 0.6s cubic-bezier(0.3, 0.7, 0.2, 1); }
    .red line { stroke: var(--linea-pista); stroke-width: 9; }
    .red circle { fill: var(--linea-pista); }
    /* El contorno, del color del suelo, borra las líneas de la pista que pasan por detrás de la cifra */
    .cifra { fill: var(--sobre-pista); paint-order: stroke; stroke: var(--pista); stroke-width: 18px; stroke-linejoin: round; }
    .cifra.campo-oscuro { stroke: color-mix(in srgb, var(--pista), #000 24%); }
  `
})
export class Pista {
  readonly probabilidadA = input.required<number>();
  readonly nombreA = input('');
  readonly nombreB = input('');

  protected readonly x0 = X0;
  protected readonly y0 = Y0;
  protected readonly largo = LARGO;
  protected readonly ancho = ANCHO;
  protected readonly pasillo = PASILLO;
  protected readonly saque = SAQUE;
  protected readonly centro = CENTRO;

  protected readonly xRed = computed(() => X0 + LARGO * this.probabilidadA());
  protected readonly desplazamiento = computed(() => `translateX(${this.xRed()}px)`);
  protected readonly textoA = computed(() => formatearProbabilidad(this.probabilidadA()));
  protected readonly textoB = computed(() => formatearProbabilidad(1 - this.probabilidadA()));

  // Cada cifra va centrada en su campo y encoge si el campo es estrecho, para no pisar la red
  protected readonly cifraA = computed(() => cifraEnCampo(X0, Math.min(this.xRed(), CENTRO - SAQUE)));
  protected readonly cifraB = computed(() => cifraEnCampo(Math.max(this.xRed(), CENTRO + SAQUE), X0 + LARGO));

  // La etiqueta del 50 % se esconde cuando la red del pronóstico pasa por encima
  protected readonly verMitad = computed(() => Math.abs(this.xRed() - CENTRO) > 70);

  protected readonly descripcion = computed(() =>
    `Probabilidad de victoria: ${this.nombreA()} ${this.textoA()}, ${this.nombreB()} ${this.textoB()}`);
}
