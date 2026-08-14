import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { PageLoader } from '@/components/product/Primitives'
import { useAuth } from '@/contexts/AuthContext'

export function RequireAuthenticated(){
  const {user,loading}=useAuth();const location=useLocation()
  if(loading)return <PageLoader/>
  if(!user)return <Navigate to="/login" replace state={{from:location.pathname}}/>
  if(user.must_change_password && location.pathname!=='/change-password')return <Navigate to="/change-password" replace/>
  return <Outlet/>
}
export function RequirePasswordChanged(){const{user}=useAuth();if(user?.must_change_password)return <Navigate to="/change-password" replace/>;return <Outlet/>}
export function RequireAdmin(){const{user}=useAuth();if(user?.role!=='admin')return <Navigate to="/" replace/>;return <Outlet/>}
