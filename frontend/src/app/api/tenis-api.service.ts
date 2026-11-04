import { HttpClient, HttpErrorResponse, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';

// Mismos formatos que backend/Models/TenisModels.cs

export type Superficie = 'Hard' | 'Clay' | 'Grass';

export interface JugadorResumen {
  id: number;
  nombre: string;
  pais: string | null;
  ranking: number | null;
  elo: number | null;
}

export interface Partido {
  fecha: string;
  torneo: string;
  nivel: string;
  superficie: string | null;
  ronda: string;
  marcador: string;
  ganadorId: number;
  ganadorNombre: string;
  perdedorId: number;
  perdedorNombre: string;
}

export interface BalanceH2H {
  victoriasA: number;
  victoriasB: number;
}

export interface H2H {
  jugadorA: JugadorResumen;
  jugadorB: JugadorResumen;
  total: BalanceH2H;
  porSuperficie: Record<string, BalanceH2H>;
  partidos: Partido[];
}

export interface Comparativa {
  clave: string;
  nombre: string;
  valorA: number | null;
  valorB: number | null;
  mayorEsMejor: boolean;
}

export interface PrediccionRequest {
  jugadorA: number;
  jugadorB: number;
  superficie: Superficie;
  nivel: string;
  ronda: string;
  mejorDe: number;
}

export interface Prediccion {
  jugadorA: JugadorResumen;
  jugadorB: JugadorResumen;
  probabilidadA: number;
  probabilidadB: number;
  probabilidadEloA: number;
  h2H: BalanceH2H;
  comparativa: Comparativa[];
  fechaDatos: string;
}

export interface Jugador {
  id: number;
  nombre: string;
  pais: string | null;
  mano: string | null;
  altura: number | null;
  fechaNacimiento: string | null;
  ranking: number | null;
  puntosRanking: number | null;
  elo: { general: number | null; dura: number | null; tierra: number | null; hierba: number | null; partidos: number | null };
  ultimoPartido: string | null;
  activo: boolean;
}

export interface EstadisticasJugador {
  ventana: '52s' | 'sup' | 'carrera';
  superficie: string;
  valores: Record<string, number | null>;
}

export interface FichaJugador {
  jugador: Jugador;
  estadisticas: EstadisticasJugador[];
  ultimosPartidos: Partido[];
}

export interface PuntoElo {
  fecha: string;
  elo: number;
}

@Injectable({ providedIn: 'root' })
export class TenisApiService {
  private readonly http = inject(HttpClient);

  buscar(texto: string): Promise<JugadorResumen[]> {
    return firstValueFrom(this.http.get<JugadorResumen[]>('/api/jugadores', { params: { buscar: texto, limite: 8 } }));
  }

  mejores(superficie: Superficie | 'General', limite = 10): Promise<JugadorResumen[]> {
    const params = new HttpParams().set('superficie', superficie).set('limite', limite);
    return firstValueFrom(this.http.get<JugadorResumen[]>('/api/jugadores/top', { params }));
  }

  ficha(id: number): Promise<FichaJugador> {
    return firstValueFrom(this.http.get<FichaJugador>(`/api/jugadores/${id}`));
  }

  historialElo(id: number): Promise<PuntoElo[]> {
    return firstValueFrom(this.http.get<PuntoElo[]>(`/api/jugadores/${id}/elo`));
  }

  h2h(a: number, b: number): Promise<H2H> {
    return firstValueFrom(this.http.get<H2H>(`/api/h2h/${a}/${b}`));
  }

  predecir(peticion: PrediccionRequest): Promise<Prediccion> {
    return firstValueFrom(this.http.post<Prediccion>('/api/prediccion', peticion));
  }
}

// La API siempre responde {"error": "..."}; si no llega eso, es que no hay API detrás
export function mensajeDeError(error: unknown): string {
  if (error instanceof HttpErrorResponse && typeof error.error?.error === 'string') return error.error.error;
  return 'No se puede conectar con la API. Arráncala con «dotnet run» en la carpeta backend.';
}
