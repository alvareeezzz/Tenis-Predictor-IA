import { Injectable, effect, signal } from '@angular/core';
import type { JugadorResumen, Superficie } from '../api/tenis-api.service';

// El partido que se está pronosticando. Vive fuera de la página para que no se pierda
// al ir a la ficha de un jugador y volver.
@Injectable({ providedIn: 'root' })
export class PronosticoEstado {
  readonly jugadorA = signal<JugadorResumen | null>(null);
  readonly jugadorB = signal<JugadorResumen | null>(null);
  readonly superficie = signal<Superficie>('Hard');
  readonly nivel = signal('G');
  readonly ronda = signal('F');

  constructor() {
    // El color de toda la app sigue a la superficie elegida (ver styles.css)
    effect(() => { document.documentElement.dataset['superficie'] = this.superficie(); });
  }

  intercambiar(): void {
    const a = this.jugadorA();
    this.jugadorA.set(this.jugadorB());
    this.jugadorB.set(a);
  }
}
