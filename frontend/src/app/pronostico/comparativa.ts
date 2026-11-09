import { Component, computed, input } from '@angular/core';
import type { Comparativa } from '../api/tenis-api.service';
import { FECHA_RANKING_ATP, formatearEstadistica } from '../formato';

interface Fila extends Comparativa {
  textoA: string;
  textoB: string;
  parteA: number;        // qué parte de la barra es de A (0 a 1)
  mejor: 'a' | 'b' | null;
}

// Estadísticas cara a cara, como en las retransmisiones: cada barra se reparte entre los dos
// y el lado del que es mejor en esa estadística lleva el color de la pista
@Component({
  selector: 'app-comparativa',
  template: `
    <ul>
      @for (f of filas(); track f.clave) {
        <li>
          <span class="nombre">{{ f.nombre }}</span>
          <span class="valor marcador" [class.mejor]="f.mejor === 'a'">{{ f.textoA }}</span>
          <span class="barra" aria-hidden="true">
            <span class="parte" [class.mejor]="f.mejor === 'a'" [style.flex-grow]="f.parteA"></span>
            <span class="parte" [class.mejor]="f.mejor === 'b'" [style.flex-grow]="1 - f.parteA"></span>
          </span>
          <span class="valor marcador derecha" [class.mejor]="f.mejor === 'b'">{{ f.textoB }}</span>
        </li>
      }
    </ul>
  `,
  styles: `
    ul { list-style: none; display: grid; gap: 20px; }
    /* Etiqueta a todo el ancho; debajo, cifra de A, barra repartida y cifra de B */
    li { display: grid; grid-template-columns: 4.75rem 1fr 4.75rem; align-items: center; column-gap: 12px; }
    .nombre { grid-column: 1 / -1; text-align: center; font-size: 0.9rem; line-height: 1.3; color: var(--gris); }
    .valor { font-size: 1.375rem; line-height: 1.2; color: var(--gris); font-weight: 600; }
    .valor.mejor { color: var(--tinta); font-weight: 700; }
    .derecha { text-align: right; }
    .barra { display: flex; gap: 3px; height: 8px; }
    .parte { background: var(--linea); border-radius: 1px; transition: flex-grow 0.5s ease; }
    .parte.mejor { background: var(--pista); }
    @media (max-width: 520px) {
      li { grid-template-columns: 4.25rem 1fr 4.25rem; column-gap: 8px; }
      .valor { font-size: 1.2rem; }
    }
  `
})
export class ComparativaEstadisticas {
  readonly datos = input.required<Comparativa[]>();

  protected readonly filas = computed<Fila[]>(() => this.datos().map(c => {
    const { valorA: a, valorB: b } = c;
    let parteA = 0.5;
    if (a !== null && b !== null) {
      if (c.clave.startsWith('elo')) parteA = 1 / (1 + 10 ** ((b - a) / 400)); // reparto según el Elo
      else if (a + b > 0) parteA = c.mayorEsMejor ? a / (a + b) : b / (a + b);
    }
    const mejor = a === null || b === null || a === b ? null : (a > b) === c.mayorEsMejor ? 'a' : 'b';
    // El ranking ATP no se actualiza desde junio de 2026 (no hay fuente abierta posterior)
    const nombre = c.clave === 'log_rank' ? `${c.nombre} (${FECHA_RANKING_ATP})` : c.nombre;
    return { ...c, nombre, parteA, mejor, textoA: formatearEstadistica(c.clave, a), textoB: formatearEstadistica(c.clave, b) };
  }));
}
