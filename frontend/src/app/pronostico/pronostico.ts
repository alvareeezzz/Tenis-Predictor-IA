import { Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { RouterLink } from '@angular/router';
import { TenisApiService, mensajeDeError, type H2H, type Prediccion, type PrediccionRequest, type Superficie } from '../api/tenis-api.service';
import { NIVELES, NOMBRE_RONDA, NOMBRE_SUPERFICIE, RONDAS, SUPERFICIES, formatearFecha, formatearProbabilidad } from '../formato';
import { BuscadorJugador } from './buscador-jugador';
import { ComparativaEstadisticas } from './comparativa';
import { Pista } from './pista';
import { PronosticoEstado } from './pronostico-estado.service';

@Component({
  selector: 'app-pronostico',
  imports: [BuscadorJugador, Pista, ComparativaEstadisticas, RouterLink],
  templateUrl: './pronostico.html',
  styleUrl: './pronostico.css'
})
export class Pronostico {
  private readonly api = inject(TenisApiService);
  protected readonly estado = inject(PronosticoEstado);

  protected readonly superficies = SUPERFICIES;
  protected readonly niveles = NIVELES;
  protected readonly rondas = RONDAS;
  protected readonly nombreRonda = NOMBRE_RONDA;
  protected readonly nombreSuperficie = NOMBRE_SUPERFICIE;
  protected readonly probabilidad = formatearProbabilidad;
  protected readonly fecha = formatearFecha;

  protected readonly prediccion = signal<Prediccion | null>(null);
  protected readonly h2h = signal<H2H | null>(null);
  protected readonly cargando = signal(false);
  protected readonly error = signal('');

  // Explicación escrita por Claude: solo se pide al pulsar el botón, porque cada una cuesta una llamada
  protected readonly explicacion = signal('');
  protected readonly explicando = signal(false);
  protected readonly errorExplicacion = signal('');
  private peticionActual: PrediccionRequest | null = null;
  private cancelarExplicacion?: AbortController;

  // Los Grand Slams se juegan al mejor de 5 sets; el resto, al mejor de 3
  protected readonly mejorDe = computed(() => (this.estado.nivel() === 'G' ? 5 : 3));

  protected readonly favorito = computed(() => {
    const p = this.prediccion();
    if (!p) return null;
    return p.probabilidadA >= 0.5 ? p.jugadorA : p.jugadorB;
  });

  private ultimaPeticion = 0;

  constructor() {
    // Primera visita: un partido entre los dos mejores por Elo, para no empezar con la pista vacía
    if (!this.estado.jugadorA() && !this.estado.jugadorB()) {
      this.api.mejores('General', 2).then(([a, b]) => {
        this.estado.jugadorA.set(a);
        this.estado.jugadorB.set(b);
      }).catch(error => this.error.set(mensajeDeError(error)));
    }

    // Cada cambio en el partido vuelve a pedir el pronóstico
    effect(() => {
      const a = this.estado.jugadorA();
      const b = this.estado.jugadorB();
      const peticion = {
        superficie: this.estado.superficie(), nivel: this.estado.nivel(),
        ronda: this.estado.ronda(), mejorDe: this.mejorDe()
      };
      if (!a || !b) return;
      untracked(() => this.pronosticar(a.id, b.id, peticion));
    });
  }

  private async pronosticar(a: number, b: number, partido: { superficie: Superficie; nivel: string; ronda: string; mejorDe: number }) {
    if (a === b) {
      this.error.set('Elige dos jugadores distintos.');
      return;
    }
    const peticion = ++this.ultimaPeticion;
    this.cargando.set(true);
    this.olvidarExplicacion(); // era de otro partido
    try {
      const datos: PrediccionRequest = { jugadorA: a, jugadorB: b, ...partido };
      const [prediccion, h2h] = await Promise.all([this.api.predecir(datos), this.api.h2h(a, b)]);
      if (peticion !== this.ultimaPeticion) return; // ya se pidió otro partido
      this.peticionActual = datos;
      this.prediccion.set(prediccion);
      this.h2h.set(h2h);
      this.error.set('');
    } catch (error) {
      if (peticion === this.ultimaPeticion) this.error.set(mensajeDeError(error));
    } finally {
      if (peticion === this.ultimaPeticion) this.cargando.set(false);
    }
  }

  protected async explicar(): Promise<void> {
    if (!this.peticionActual) return;
    this.olvidarExplicacion();
    const cancelar = (this.cancelarExplicacion = new AbortController());
    this.explicando.set(true);
    try {
      for await (const trozo of this.api.explicar(this.peticionActual, cancelar.signal))
        this.explicacion.update(texto => texto + trozo);
    } catch (error) {
      if (!cancelar.signal.aborted) this.errorExplicacion.set(error instanceof Error ? error.message : String(error));
    } finally {
      if (this.cancelarExplicacion === cancelar) this.explicando.set(false);
    }
  }

  private olvidarExplicacion(): void {
    this.cancelarExplicacion?.abort();
    this.cancelarExplicacion = undefined;
    this.explicacion.set('');
    this.errorExplicacion.set('');
    this.explicando.set(false);
  }

  protected cambiarNivel(nivel: string): void {
    this.estado.nivel.set(nivel);
    // La ATP Finals y la Copa Davis no tienen rondas de 128 o 64
    if (['F', 'D'].includes(nivel) && ['R128', 'R64', 'R32'].includes(this.estado.ronda())) this.estado.ronda.set('SF');
  }
}
