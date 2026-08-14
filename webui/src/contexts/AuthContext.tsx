import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { authApi, setCsrfToken, systemApi, type AuthUser } from '@/lib/api'

interface AuthContextValue {
  user: AuthUser | null
  loading: boolean
  remote: boolean
  login: (username:string,password:string)=>Promise<AuthUser>
  changePassword: (password:string,confirmation:string)=>Promise<AuthUser>
  logout: ()=>Promise<void>
  refresh: ()=>Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({children}:{children:ReactNode}) {
  const [user,setUser] = useState<AuthUser|null>(null)
  const [loading,setLoading] = useState(true)
  const [remote,setRemote] = useState(false)
  const apply = useCallback((data:{user:AuthUser;csrf_token:string|null}) => {
    setUser(data.user); setCsrfToken(data.csrf_token)
  },[])
  const refresh = useCallback(async() => {
    setLoading(true)
    try {
      const caps = await systemApi.capabilities()
      const isRemote = caps.data.mode === 'remote'; setRemote(isRemote)
      if (isRemote) apply((await authApi.me()).data)
      else setUser({user_id:'local_owner',username:'local_owner',display_name:'本机管理员',role:'admin',status:'active',must_change_password:false})
    } catch { setUser(null); setCsrfToken(null) }
    finally { setLoading(false) }
  },[apply])
  useEffect(()=>{ void refresh() },[refresh])
  const value = useMemo<AuthContextValue>(()=>({
    user,loading,remote,refresh,
    login: async(username,password)=>{const response=await authApi.login(username,password);apply(response.data);return response.data.user},
    changePassword: async(password,confirmation)=>{const response=await authApi.changePassword(password,confirmation);apply(response.data);return response.data.user},
    logout: async()=>{try{await authApi.logout()}finally{setUser(null);setCsrfToken(null)}},
  }),[user,loading,remote,refresh,apply])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(){const value=useContext(AuthContext);if(!value)throw new Error('useAuth must be used inside AuthProvider');return value}
