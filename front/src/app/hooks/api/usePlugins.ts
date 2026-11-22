import { useMutation, useQuery } from "@tanstack/react-query";
import toast from "react-hot-toast";

import api from "@/app/api";

export default function usePlugins() {
  const query = useQuery({
    queryKey: ["plugins"],
    queryFn: api.plugins.get,
  });

  const refetch = () => {
    void query.refetch();
  };

  const enable = useMutation({
    mutationFn: ({ id, grants }: { id: string; grants?: string[] }) =>
      api.plugins.enable(id, grants),
    onSuccess: (plugin) => {
      toast.success(`Enabled ${plugin.name}`);
      refetch();
    },
    onError: () => toast.error("Could not enable plugin"),
  });

  const disable = useMutation({
    mutationFn: (kind: string) => api.plugins.disable(kind),
    onSuccess: (plugin) => {
      toast.success(`Disabled ${plugin.name}`);
      refetch();
    },
    onError: () => toast.error("Could not disable plugin"),
  });

  const restart = useMutation({
    mutationFn: (id: string) => api.plugins.restart(id),
    onSuccess: (plugin) => {
      toast.success(`Restarted ${plugin.name}`);
      refetch();
    },
    onError: () => toast.error("Could not restart plugin"),
  });

  return {
    items: query.data,
    total: query.data?.length,
    query,
    actions: { enable, disable, restart },
  };
}
