import type { Superficie } from './api/tenis-api.service';

export const SUPERFICIES: { valor: Superficie; nombre: string }[] = [
  { valor: 'Hard', nombre: 'Dura' },
  { valor: 'Clay', nombre: 'Tierra' },
  { valor: 'Grass', nombre: 'Hierba' }
];

export const NOMBRE_SUPERFICIE: Record<string, string | undefined> = {
  Hard: 'Dura', Clay: 'Tierra', Grass: 'Hierba', Carpet: 'Moqueta'
};

// Último ranking ATP disponible: ninguna fuente abierta publica los posteriores
export const FECHA_RANKING_ATP = '8 jun 2026';

// "Jannik Sinner" → "Sinner", "Alex De Minaur" → "De Minaur": todo menos el nombre de pila
export function apellido(nombre: string): string {
  const espacio = nombre.indexOf(' ');
  return espacio < 0 ? nombre : nombre.slice(espacio + 1);
}

export const NIVELES = [
  { valor: 'G', nombre: 'Grand Slam' },
  { valor: 'M', nombre: 'Masters 1000' },
  { valor: 'A', nombre: 'ATP 250 / 500' },
  { valor: 'F', nombre: 'ATP Finals' },
  { valor: 'D', nombre: 'Copa Davis' },
  { valor: 'O', nombre: 'Juegos Olímpicos' }
];

export const RONDAS = [
  { valor: 'R128', nombre: 'Primera ronda (128)' },
  { valor: 'R64', nombre: 'Segunda ronda (64)' },
  { valor: 'R32', nombre: 'Dieciseisavos' },
  { valor: 'R16', nombre: 'Octavos' },
  { valor: 'QF', nombre: 'Cuartos' },
  { valor: 'SF', nombre: 'Semifinal' },
  { valor: 'F', nombre: 'Final' },
  { valor: 'RR', nombre: 'Fase de grupos' }
];

export const NOMBRE_RONDA: Record<string, string | undefined> = {
  ...Object.fromEntries(RONDAS.map(r => [r.valor, r.nombre])),
  Q1: 'Previa 1', Q2: 'Previa 2', Q3: 'Previa 3', BR: 'Bronce', ER: 'Primera ronda'
};

const porcentaje = new Intl.NumberFormat('es-ES', { style: 'percent', maximumFractionDigits: 1 });
const entero = new Intl.NumberFormat('es-ES', { maximumFractionDigits: 0 });
const decimal = new Intl.NumberFormat('es-ES', { minimumFractionDigits: 1, maximumFractionDigits: 2 });

// Cada estadística se enseña en su unidad: porcentajes, ratios, conteos...
export function formatearEstadistica(clave: string, valor: number | null | undefined): string {
  if (valor === null || valor === undefined) return '–';
  if (clave.startsWith('pct_')) return porcentaje.format(valor);
  if (clave.startsWith('victorias_ult')) return `${Math.round(valor * Number(clave.replace('victorias_ult', '')))} de ${clave.replace('victorias_ult', '')}`;
  if (clave.startsWith('elo') || clave === 'log_rank' || clave.startsWith('partidos') || clave.startsWith('tiebreaks_jugados')) return entero.format(valor);
  return decimal.format(valor);
}

export function formatearProbabilidad(p: number): string {
  return `${Math.round(p * 100)} %`;
}

export function formatearFecha(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString('es-ES', { day: 'numeric', month: 'short', year: 'numeric' });
}
