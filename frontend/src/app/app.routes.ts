import { Routes } from '@angular/router';
import { Pronostico } from './pronostico/pronostico';

export const routes: Routes = [
  { path: '', component: Pronostico, title: 'Tenis Predictor' },
  { path: 'ranking', loadComponent: () => import('./ranking/ranking').then(m => m.Ranking), title: 'Ranking Elo | Tenis Predictor' },
  { path: 'jugador/:id', loadComponent: () => import('./jugador/jugador').then(m => m.Jugador), title: 'Jugador | Tenis Predictor' },
  { path: '**', redirectTo: '' }
];
