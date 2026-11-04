import { Component, effect, inject, input, output, signal } from '@angular/core';
import { TenisApiService, mensajeDeError, type JugadorResumen } from '../api/tenis-api.service';

let siguienteId = 0;

// Campo de texto con sugerencias (patrón combobox de WAI-ARIA): flechas para moverse, Intro para elegir
@Component({
  selector: 'app-buscador-jugador',
  template: `
    <label class="etiqueta" [for]="id">{{ etiqueta() }}</label>
    <div class="caja">
      <input
        [id]="id" type="text" role="combobox" autocomplete="off" spellcheck="false"
        placeholder="Escribe un apellido"
        [attr.aria-expanded]="abierto()" [attr.aria-controls]="id + '-lista'"
        [attr.aria-activedescendant]="activo() >= 0 ? id + '-' + activo() : null"
        [value]="texto()" (input)="escribir($any($event.target).value)"
        (keydown)="tecla($event)" (focus)="$any($event.target).select()" (blur)="cerrarPronto()" />
      @if (abierto()) {
        <ul [id]="id + '-lista'" role="listbox" class="lista">
          @for (j of resultados(); track j.id; let i = $index) {
            <li [id]="id + '-' + i" role="option" [attr.aria-selected]="i === activo()"
                [class.activo]="i === activo()" (mousedown)="elegir(j)">
              <span>{{ j.nombre }}</span>
              <span class="secundario cifras">{{ j.ranking ? 'n.º ' + j.ranking + ', ' : '' }}{{ j.pais }}</span>
            </li>
          } @empty {
            <li class="vacio secundario">{{ aviso() }}</li>
          }
        </ul>
      }
    </div>
  `,
  styles: `
    :host { display: block; position: relative; }
    .etiqueta { display: block; font-size: 0.875rem; color: var(--gris); margin-bottom: 4px; }
    input {
      width: 100%; padding: 10px 12px; border: 1px solid var(--linea); border-radius: 4px;
      background: var(--papel); font-weight: 600;
    }
    input:focus { border-color: var(--pista-texto); outline: none; box-shadow: 0 0 0 1px var(--pista-texto); }
    .lista {
      position: absolute; z-index: 10; left: 0; right: 0; margin-top: 4px; list-style: none;
      background: var(--papel); border: 1px solid var(--linea); border-radius: 4px; max-height: 320px; overflow-y: auto;
    }
    li { display: flex; justify-content: space-between; gap: 12px; padding: 8px 12px; cursor: pointer; }
    li.activo { background: var(--fondo); box-shadow: inset 3px 0 0 var(--pista); }
    li.vacio { cursor: default; }
  `
})
export class BuscadorJugador {
  private readonly api = inject(TenisApiService);

  readonly etiqueta = input.required<string>();
  readonly jugador = input<JugadorResumen | null>(null);
  readonly elegido = output<JugadorResumen>();

  protected readonly id = `buscador-${siguienteId++}`;
  protected readonly texto = signal('');
  protected readonly resultados = signal<JugadorResumen[]>([]);
  protected readonly abierto = signal(false);
  protected readonly activo = signal(-1);
  protected readonly aviso = signal('');
  private espera?: ReturnType<typeof setTimeout>;
  private ultimaBusqueda = 0;

  constructor() {
    // Si el jugador cambia desde fuera (al intercambiar), el campo enseña su nombre
    effect(() => this.texto.set(this.jugador()?.nombre ?? ''));
  }

  protected escribir(valor: string): void {
    this.texto.set(valor);
    clearTimeout(this.espera);
    if (valor.trim().length < 2) {
      this.abierto.set(false);
      return;
    }
    this.espera = setTimeout(() => this.buscar(valor), 200);
  }

  private async buscar(valor: string): Promise<void> {
    const busqueda = ++this.ultimaBusqueda;
    try {
      const resultados = await this.api.buscar(valor);
      if (busqueda !== this.ultimaBusqueda) return; // llegó tarde: ya se escribió otra cosa
      this.resultados.set(resultados);
      this.aviso.set('Ningún jugador en activo con ese nombre.');
    } catch (error) {
      this.resultados.set([]);
      this.aviso.set(mensajeDeError(error));
    }
    this.activo.set(this.resultados().length ? 0 : -1);
    this.abierto.set(true);
  }

  protected tecla(evento: KeyboardEvent): void {
    if (!this.abierto()) return;
    const total = this.resultados().length;
    if (evento.key === 'ArrowDown' && total) {
      evento.preventDefault();
      this.activo.update(i => (i + 1) % total);
    } else if (evento.key === 'ArrowUp' && total) {
      evento.preventDefault();
      this.activo.update(i => (i - 1 + total) % total);
    } else if (evento.key === 'Enter' && this.activo() >= 0) {
      evento.preventDefault();
      this.elegir(this.resultados()[this.activo()]);
    } else if (evento.key === 'Escape') {
      this.cerrar();
    }
  }

  protected elegir(jugador: JugadorResumen): void {
    this.elegido.emit(jugador);
    this.texto.set(jugador.nombre);
    this.cerrar();
  }

  // Al salir del campo sin elegir, vuelve el nombre del jugador que ya estaba
  protected cerrarPronto(): void {
    setTimeout(() => {
      this.cerrar();
      this.texto.set(this.jugador()?.nombre ?? '');
    }, 150);
  }

  private cerrar(): void {
    this.abierto.set(false);
    this.activo.set(-1);
  }
}
