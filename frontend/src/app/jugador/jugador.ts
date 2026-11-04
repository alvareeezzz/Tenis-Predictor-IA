import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { TenisApiService, mensajeDeError, type FichaJugador, type PuntoElo } from '../api/tenis-api.service';
import { NOMBRE_RONDA, NOMBRE_SUPERFICIE, formatearEstadistica, formatearFecha } from '../formato';
import { PronosticoEstado } from '../pronostico/pronostico-estado.service';
import { GraficaElo } from './grafica-elo';

// Estadísticas de la tabla de la ficha, en el orden en que se leen: saque, resto, global
const FILAS = [
  { clave: 'aces_partido', nombre: 'Aces por partido' },
  { clave: 'dobles_faltas_partido', nombre: 'Dobles faltas por partido' },
  { clave: 'pct_primer_saque', nombre: '% de primer saque' },
  { clave: 'pct_ganados_1er_saque', nombre: '% de puntos ganados con el 1.º' },
  { clave: 'pct_ganados_2do_saque', nombre: '% de puntos ganados con el 2.º' },
  { clave: 'pct_juegos_saque', nombre: '% de juegos de saque ganados' },
  { clave: 'pct_bp_salvados', nombre: '% de break points salvados' },
  { clave: 'pct_puntos_resto', nombre: '% de puntos ganados al resto' },
  { clave: 'pct_juegos_resto', nombre: '% de juegos al resto ganados' },
  { clave: 'pct_bp_convertidos', nombre: '% de break points convertidos' },
  { clave: 'pct_puntos_totales', nombre: '% de puntos ganados' },
  { clave: 'dominance_ratio', nombre: 'Dominance ratio' },
  { clave: 'pct_tiebreaks', nombre: '% de tie-breaks ganados' },
  { clave: 'pct_victorias', nombre: '% de victorias' },
  { clave: 'partidos', nombre: 'Partidos' }
];

const COLUMNAS = [
  { ventana: '52s', superficie: 'Todas', nombre: 'Últimas 52 semanas' },
  { ventana: 'sup', superficie: 'Hard', nombre: 'Dura (2 años)' },
  { ventana: 'sup', superficie: 'Clay', nombre: 'Tierra (2 años)' },
  { ventana: 'sup', superficie: 'Grass', nombre: 'Hierba (2 años)' },
  { ventana: 'carrera', superficie: 'Todas', nombre: 'Carrera' }
];

@Component({
  selector: 'app-jugador',
  imports: [RouterLink, GraficaElo],
  templateUrl: './jugador.html',
  styleUrl: './jugador.css'
})
export class Jugador {
  private readonly api = inject(TenisApiService);
  private readonly estado = inject(PronosticoEstado);
  private readonly router = inject(Router);

  readonly id = input.required<string>(); // parámetro de la ruta /jugador/:id

  protected readonly ficha = signal<FichaJugador | null>(null);
  protected readonly elo = signal<PuntoElo[]>([]);
  protected readonly error = signal('');
  protected readonly columnas = COLUMNAS;
  protected readonly fecha = formatearFecha;
  protected readonly nombreRonda = NOMBRE_RONDA;
  protected readonly nombreSuperficie = NOMBRE_SUPERFICIE;

  protected readonly edad = computed(() => {
    const nacimiento = this.ficha()?.jugador.fechaNacimiento;
    if (!nacimiento) return null;
    return Math.floor((Date.now() - Date.parse(nacimiento)) / (365.25 * 24 * 3600 * 1000));
  });

  protected readonly tabla = computed(() => {
    const ficha = this.ficha();
    if (!ficha) return [];
    const buscar = (ventana: string, superficie: string) =>
      ficha.estadisticas.find(e => e.ventana === ventana && e.superficie === superficie)?.valores;
    return FILAS.map(f => ({
      nombre: f.nombre,
      valores: COLUMNAS.map(c => formatearEstadistica(f.clave, buscar(c.ventana, c.superficie)?.[f.clave]))
    }));
  });

  constructor() {
    effect(() => this.cargar(Number(this.id())));
  }

  private async cargar(id: number): Promise<void> {
    this.ficha.set(null);
    try {
      const [ficha, elo] = await Promise.all([this.api.ficha(id), this.api.historialElo(id)]);
      this.ficha.set(ficha);
      this.elo.set(elo);
      this.error.set('');
    } catch (error) {
      this.error.set(mensajeDeError(error));
    }
  }

  protected pronosticar(): void {
    const j = this.ficha()!.jugador;
    this.estado.jugadorA.set({ id: j.id, nombre: j.nombre, pais: j.pais, ranking: j.ranking, elo: j.elo.general });
    if (this.estado.jugadorB()?.id === j.id) this.estado.jugadorB.set(null);
    this.router.navigateByUrl('/');
  }

  protected entero(valor: number | null): string {
    return valor === null ? '–' : valor.toLocaleString('es-ES', { maximumFractionDigits: 0 });
  }
}
