import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { useSettingsStore } from "@/stores/settings";

const schema = z.object({
  user: z.string().min(1, "用户名不能为空"),
  password: z.string().min(1, "密码不能为空"),
});

type FormValues = z.infer<typeof schema>;

export function MilvusCredentialsForm() {
  const milvusConfigured = useSettingsStore((s) => s.milvusConfigured);
  const setMilvusConfigured = useSettingsStore((s) => s.setMilvusConfigured);
  const [saved, setSaved] = useState(false);
  const [showForm, setShowForm] = useState(false);

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<FormValues>({ defaultValues: { user: "", password: "" } });

  const onSubmit = async (values: FormValues) => {
    await window.api.settings.setMilvusCredentials(values.user, values.password);
    setMilvusConfigured(true);
    setSaved(true);
    setShowForm(false);
  };

  if (milvusConfigured && !showForm) {
    return (
      <div className="space-y-2">
        <p className="text-sm text-green-700">Milvus 凭证已配置</p>
        <button
          type="button"
          className="rounded border border-neutral-300 px-3 py-1 text-sm hover:bg-neutral-100"
          onClick={() => setShowForm(true)}
        >
          重新配置
        </button>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="space-y-3">
      <div className="text-sm font-medium">Milvus 凭证</div>
      <div>
        <label className="block text-xs text-neutral-500">用户名</label>
        <input
          {...register("user", {
            validate: (v) =>
              schema.shape.user.safeParse(v).success || "用户名不能为空",
          })}
          className="w-full rounded border border-neutral-300 px-2 py-1 text-sm"
        />
        {errors.user && (
          <span className="text-xs text-red-600">{errors.user.message}</span>
        )}
      </div>
      <div>
        <label className="block text-xs text-neutral-500">密码</label>
        <input
          type="password"
          {...register("password", {
            validate: (v) =>
              schema.shape.password.safeParse(v).success || "密码不能为空",
          })}
          className="w-full rounded border border-neutral-300 px-2 py-1 text-sm"
        />
        {errors.password && (
          <span className="text-xs text-red-600">{errors.password.message}</span>
        )}
      </div>
      <button
        type="submit"
        className="rounded bg-neutral-800 px-3 py-1 text-sm text-white hover:bg-neutral-700"
      >
        保存
      </button>
      {saved && <span className="ml-2 text-xs text-green-700">已保存</span>}
    </form>
  );
}
