import { Component, computed, effect, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { TenisApiService, mensajeDeError, type JugadorResumen, type Superficie } from '../api/tenis-api.service';
import { FECHA_RANKING_ATP, SUPERFICIES } from '../formato';
import { PronosticoEstado } from '../pronostico/pronostico-estado.service';

// Los 50 mejores por Elo entre quienes han jugado en el último año, en general o por superficie
@Component({
  selector: 'app-ranking',
  imports: [RouterLink],
  template: `
    <div class="contenedor pagina">
      <h1 class="marcador">Ranking Elo</h1>
      <p class="secundario intro">
        El Elo mide el nivel según a quién se gana y a quién se pierde, partido a partido. A diferencia del
        ranking ATP, no depende de cuántos torneos se juegan.
      </p>

      <div class="filtro" role="radiogroup" aria-label="Superficie">
        @for (s of opciones; track s.valor) {
          <button type="button" role="radio" [attr.aria-checked]="superficie() === s.valor"
                  [class.elegida]="superficie() === s.valor" (click)="elegir(s.valor)">{{ s.nombre }}</button>
        }
      </div>

      @if (error()) {
        <p class="error" role="alert">{{ error() }}</p>
      }

      <table class="tabla">
        <thead>
          <tr>
            <th class="num">Puesto</th><th>Jugador</th><th class="num">Elo</th>
            <th class="distancia"><span class="solo-lector">Distancia al primero</span></th>
            <th class="num">Ranking ATP <span class="fecha-atp">({{ fechaRankingAtp }})</span></th>
          </tr>
        </thead>
        <tbody>
          @for (j of filas(); track j.id; let i = $index) {
            <tr>
              <td class="num puesto">{{ i + 1 }}</td>
              <td><a [routerLink]="['/jugador', j.id]">{{ j.nombre }}</a><span class="secundario pais">{{ j.pais }}</span></td>
              <td class="num marcador elo">{{ j.elo?.toLocaleString('es-ES', { maximumFractionDigits: 0 }) }}</td>
              <td class="distancia" aria-hidden="true"><span class="barra" [style.width.%]="j.barra"></span></td>
              <td class="num secundario">{{ j.ranking ?? '–' }}</td>
            </tr>
          }
        </tbody>
      </table>
    </div>
  `,
  styles: `
    .pagina { padding-top: 32px; padding-bottom: 48px; }
    .pais { margin-left: 0.5em; font-size: 0.9rem; }
    .puesto { color: var(--gris); }
    .elo { font-size: 1.15rem; }
    .fecha-atp { font-weight: 400; }
    /* Distancia al n.º 1: la barra del primero llena la columna */
    .distancia { width: 32%; }
    td.distancia { vertical-align: middle; }
    .barra { display: block; height: 6px; background: var(--pista); border-radius: 1px; }
    @media (max-width: 620px) { .distancia { display: none; } }
    h1 { font-size: clamp(2.2rem, 7vw, 3.5rem); }
    .intro { margin: 8px 0 24px; max-width: 60ch; }
    .filtro { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 20px; }
    .filtro button {
      padding: 8px 16px; border-radius: 4px; border: 1px solid var(--linea); background: var(--papel);
      cursor: pointer; font-weight: 600;
    }
    .filtro button.elegida { background: var(--pista); border-color: var(--pista); color: var(--sobre-pista); }
  `
})
export class Ranking {
  private readonly api = inject(TenisApiService);
  private readonly estado = inject(PronosticoEstado);

  protected readonly opciones: { valor: Superficie | 'General'; nombre: string }[] =
    [{ valor: 'General', nombre: 'General' }, ...SUPERFICIES];
  protected readonly superficie = signal<Superficie | 'General'>('General');
  protected readonly jugadores = signal<JugadorResumen[]>([]);
  protected readonly error = signal('');
  protected readonly fechaRankingAtp = FECHA_RANKING_ATP;

  // La barra empieza 100 puntos por debajo del último de la lista, para que el último no quede vacío
  protected readonly filas = computed(() => {
    const js = this.jugadores();
    const elos = js.map(j => j.elo ?? 0);
    const max = Math.max(...elos), base = Math.min(...elos) - 100;
    return js.map(j => ({ ...j, barra: ((j.elo ?? base) - base) / Math.max(max - base, 1) * 100 }));
  });

  constructor() {
    effect(() => {
      this.api.mejores(this.superficie(), 50)
        .then(j => { this.jugadores.set(j); this.error.set(''); })
        .catch(e => this.error.set(mensajeDeError(e)));
    });
  }

  protected elegir(superficie: Superficie | 'General'): void {
    this.superficie.set(superficie);
    // El color de la app sigue a la superficie, como en el pronóstico
    if (superficie !== 'General') this.estado.superficie.set(superficie);
  }
}
